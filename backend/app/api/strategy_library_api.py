from datetime import datetime
from statistics import mean
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.signal_registry import SignalDetector, SignalRegistry
from app.core.signals import register_indicator_signals, register_pattern_signals, register_structure_signals
from app.db.session import get_db
from app.models.models import EdgeStat, MarketCandle, SignalEvent, StrategyBacktestRun, Watchlist
from app.schemas.strategies import (
    StrategyBacktestSummary,
    StrategyBacktestTradesResponse,
    StrategyBacktestsResponse,
    StrategyCard,
    StrategyDetail,
    StrategySymbolPerf,
    StrategyTrade,
    StrategyTradesResponse,
)
from app.tasks.edge_discovery import run_edge_discovery_for_pair

router = APIRouter()

SORT_FIELDS = {"win_rate", "expectancy", "avg_r", "sample_size", "last_fired_at"}
ENTRY_SIGNAL_META = {
    "ma_breakout": ("均线突破", "价格收盘向上穿越均线时触发做多，跌回均线下方触发反向信号。", "按多级别回测中配置的均线周期生成入场点。"),
    "macd_divergence": ("MACD 背离", "识别价格与 MACD 柱体的底背离/顶背离。", "价格创新低但 MACD 动能不再创新低时做多，反向为做空。"),
    "sr_breakout": ("支撑阻力突破", "基于近期高低点触碰次数形成支撑阻力位。", "价格突破已确认的关键位后触发入场信号。"),
    "engulfing": ("吞没形态", "识别 K 线实体吞没前一根实体的反转形态。", "阳线吞没触发做多，阴线吞没触发做空。"),
    "w_bottom": ("W底双底", "识别双底后的颈线突破机会。", "第二个低点形成后向上突破颈线触发做多。"),
}


def _registry() -> SignalRegistry:
    registry = SignalRegistry()
    register_indicator_signals(registry)
    register_pattern_signals(registry)
    register_structure_signals(registry)
    return registry


def _meta(detector: SignalDetector) -> dict[str, Any]:
    return {
        "id": detector.name,
        "name": getattr(detector, "display_name", detector.name),
        "category": getattr(detector, "category", "indicator"),
        "description": getattr(detector, "description", ""),
        "logic": getattr(detector, "logic", ""),
        "direction_support": getattr(detector, "direction_support", ["long", "short"]),
        "params_schema": getattr(detector, "params_schema", {}),
    }


def _edge_contains_strategy(edge: EdgeStat, strategy_id: str) -> bool:
    entry = edge.combo_spec.get("entry", []) if edge.combo_spec else []
    return strategy_id in entry


def _confidence(sample_size: int) -> str:
    if sample_size < 30:
        return "low"
    if sample_size < 100:
        return "mid"
    return "high"


async def _all_edges(db: AsyncSession) -> list[EdgeStat]:
    result = await db.execute(select(EdgeStat))
    return list(result.scalars().all())


async def _all_backtests(db: AsyncSession) -> list[StrategyBacktestRun]:
    result = await db.execute(select(StrategyBacktestRun).order_by(StrategyBacktestRun.created_at.desc()))
    return list(result.scalars().all())


def _summary(run: StrategyBacktestRun) -> StrategyBacktestSummary:
    return StrategyBacktestSummary(
        id=run.id,
        strategy_id=run.strategy_id,
        strategy_kind=run.strategy_kind,
        strategy_name=run.strategy_name,
        symbol=run.symbol,
        timeframe=run.timeframe,
        trend_tf=run.trend_tf,
        start_date=run.start_date,
        end_date=run.end_date,
        total_return_pct=run.total_return_pct,
        annualized_return_pct=run.annualized_return_pct,
        max_drawdown_pct=run.max_drawdown_pct,
        sharpe_ratio=run.sharpe_ratio,
        win_rate_pct=run.win_rate_pct,
        total_trades=run.total_trades,
        winning_trades=run.winning_trades,
        losing_trades=run.losing_trades,
        buy_hold_return_pct=run.buy_hold_return_pct,
        created_at=run.created_at,
    )


def _latest_by_strategy(runs: list[StrategyBacktestRun]) -> dict[str, StrategyBacktestRun]:
    latest: dict[str, StrategyBacktestRun] = {}
    for run in runs:
        if run.strategy_id not in latest:
            latest[run.strategy_id] = run
    return latest


async def _last_fired_map(db: AsyncSession, strategy_id: str) -> dict[tuple[str, str, str], datetime | None]:
    result = await db.execute(
        select(
            SignalEvent.symbol,
            SignalEvent.timeframe,
            SignalEvent.direction,
            func.max(SignalEvent.fired_at_ts),
        )
        .where(SignalEvent.signal_name == strategy_id)
        .group_by(SignalEvent.symbol, SignalEvent.timeframe, SignalEvent.direction)
    )
    out: dict[tuple[str, str, str], datetime | None] = {}
    for symbol, timeframe, direction, ts in result.all():
        out[(symbol, timeframe, direction)] = datetime.fromtimestamp(ts) if ts else None
    return out


async def _card_for(
    db: AsyncSession,
    detector: SignalDetector,
    edges: list[EdgeStat] | None = None,
    backtests: list[StrategyBacktestRun] | None = None,
) -> StrategyCard:
    stats = [edge for edge in (edges if edges is not None else await _all_edges(db)) if _edge_contains_strategy(edge, detector.name)]
    runs = [run for run in (backtests if backtests is not None else await _all_backtests(db)) if run.strategy_id == detector.name]
    mature = [edge.win_rate for edge in stats if edge.sample_size >= 30]
    mature.extend(run.win_rate_pct / 100 for run in runs if run.total_trades >= 1)
    top_edge = max(stats, key=lambda edge: edge.win_rate, default=None)
    top_run = max(runs, key=lambda run: run.win_rate_pct, default=None)
    if top_run and (not top_edge or top_run.win_rate_pct / 100 >= top_edge.win_rate):
        top_symbol = f"{top_run.symbol} {top_run.timeframe}"
    else:
        top_symbol = f"{top_edge.symbol} {top_edge.timeframe}" if top_edge else None
    latest = runs[0] if runs else None
    return StrategyCard(
        **_meta(detector),
        coverage=len({edge.symbol for edge in stats} | {run.symbol for run in runs}),
        avg_win_rate=mean(mature) if mature else None,
        top_symbol=top_symbol,
        latest_backtest=_summary(latest) if latest else None,
    )


def _synthetic_card(strategy_id: str, runs: list[StrategyBacktestRun]) -> StrategyCard:
    latest = runs[0]
    if strategy_id in ENTRY_SIGNAL_META:
        name, description, logic = ENTRY_SIGNAL_META[strategy_id]
        category = "pattern"
    else:
        name = latest.strategy_name
        description = "由多级别回测参数自动生成的组合策略。"
        logic = "组合高级别趋势过滤、Elder Impulse 动量过滤、多个入场信号与出场规则。"
        category = "combo"
    return StrategyCard(
        id=strategy_id,
        name=name,
        category=category,
        description=description,
        logic=logic,
        direction_support=["long", "short"],
        params_schema={},
        coverage=len({run.symbol for run in runs}),
        avg_win_rate=mean([run.win_rate_pct / 100 for run in runs]) if runs else None,
        top_symbol=f"{latest.symbol} {latest.timeframe}",
        latest_backtest=_summary(latest),
    )


@router.get("", response_model=list[StrategyCard])
async def list_strategies(db: AsyncSession = Depends(get_db)):
    registry = _registry()
    edges = await _all_edges(db)
    backtests = await _all_backtests(db)
    cards = [await _card_for(db, registry.get(name), edges, backtests) for name in registry.names]
    known = set(registry.names)
    by_strategy: dict[str, list[StrategyBacktestRun]] = {}
    for run in backtests:
        by_strategy.setdefault(run.strategy_id, []).append(run)
    for strategy_id, runs in by_strategy.items():
        if strategy_id not in known:
            cards.append(_synthetic_card(strategy_id, runs))
    return cards


@router.get("/{strategy_id}", response_model=StrategyDetail)
async def strategy_detail(
    strategy_id: str,
    timeframe: str = Query("4h"),
    direction: str = Query("all"),
    sort_by: str = Query("win_rate"),
    sort_dir: str = Query("desc"),
    min_samples: int = Query(10, ge=0),
    symbols: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    registry = _registry()
    backtests = [run for run in await _all_backtests(db) if run.strategy_id == strategy_id]
    if strategy_id not in registry.names and not backtests:
        raise HTTPException(status_code=404, detail="策略不存在")
    if sort_by not in SORT_FIELDS:
        raise HTTPException(status_code=400, detail="不支持的排序字段")
    if sort_dir not in {"asc", "desc"}:
        raise HTTPException(status_code=400, detail="sort_dir 必须是 asc 或 desc")

    symbol_set = {item.strip().upper() for item in symbols.split(",")} if symbols else None
    edges = [
        edge for edge in await _all_edges(db)
        if _edge_contains_strategy(edge, strategy_id)
        and edge.timeframe == timeframe
        and edge.sample_size >= min_samples
        and (direction == "all" or edge.direction == direction)
        and (symbol_set is None or edge.symbol in symbol_set)
    ]
    last_fired = await _last_fired_map(db, strategy_id)
    rows = [
        StrategySymbolPerf(
            symbol=edge.symbol,
            timeframe=edge.timeframe,
            direction=edge.direction,
            sample_size=edge.sample_size,
            win_rate=edge.win_rate,
            avg_r=edge.avg_r,
            expectancy=edge.expectancy,
            max_drawdown=0.0,
            last_fired_at=last_fired.get((edge.symbol, edge.timeframe, edge.direction)),
            confidence=_confidence(edge.sample_size),
            source="edge",
        )
        for edge in edges
    ]
    latest_seen: set[tuple[str, str]] = set()
    for run in backtests:
        if run.timeframe != timeframe or run.total_trades < min_samples:
            continue
        key = (run.symbol, run.timeframe)
        if key in latest_seen:
            continue
        latest_seen.add(key)
        rows.append(StrategySymbolPerf(
            symbol=run.symbol,
            timeframe=run.timeframe,
            direction="long",
            sample_size=run.total_trades,
            win_rate=run.win_rate_pct / 100,
            avg_r=run.total_return_pct / 100,
            expectancy=run.total_return_pct / 100,
            max_drawdown=run.max_drawdown_pct,
            last_fired_at=run.created_at,
            confidence=_confidence(run.total_trades),
            source="backtest",
            latest_backtest_run_id=run.id,
        ))
    reverse = sort_dir == "desc"
    rows.sort(key=lambda row: getattr(row, sort_by) or datetime.min, reverse=reverse)
    card = await _card_for(db, registry.get(strategy_id), backtests=backtests) if strategy_id in registry.names else _synthetic_card(strategy_id, backtests)
    return StrategyDetail(
        card=card,
        rows=rows,
        sort_by=sort_by,
        sort_dir=sort_dir,
        generated_at=datetime.utcnow(),
    )


@router.get("/{strategy_id}/backtests", response_model=StrategyBacktestsResponse)
async def strategy_backtests(
    strategy_id: str,
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(StrategyBacktestRun)
        .where(StrategyBacktestRun.strategy_id == strategy_id)
        .order_by(StrategyBacktestRun.created_at.desc())
        .limit(limit)
    )
    items = [_summary(run) for run in result.scalars().all()]
    return StrategyBacktestsResponse(items=items, total=len(items))


@router.get("/backtests/{run_id}/trades", response_model=StrategyBacktestTradesResponse)
async def strategy_backtest_trades(
    run_id: int,
    db: AsyncSession = Depends(get_db),
):
    run = await db.get(StrategyBacktestRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="回测记录不存在")
    trades = run.payload.get("trades", []) if run.payload else []
    return StrategyBacktestTradesResponse(items=trades, total=len(trades))


@router.get("/{strategy_id}/trades", response_model=StrategyTradesResponse)
async def strategy_trades(
    strategy_id: str,
    symbol: str = Query(..., min_length=1),
    timeframe: str = Query("4h"),
    direction: str = Query("all"),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    registry = _registry()
    if strategy_id not in registry.names:
        raise HTTPException(status_code=404, detail="策略不存在")
    stmt = (
        select(SignalEvent)
        .where(
            SignalEvent.signal_name == strategy_id,
            SignalEvent.symbol == symbol.upper(),
            SignalEvent.timeframe == timeframe,
        )
        .order_by(SignalEvent.fired_at_ts.desc())
        .limit(limit)
    )
    if direction != "all":
        stmt = stmt.where(SignalEvent.direction == direction)
    result = await db.execute(stmt)
    events = list(result.scalars().all())
    items: list[StrategyTrade] = []
    for event in events:
        exit_price = None
        pnl_pct = None
        if event.realized_r is not None:
            risk = max(float(event.fired_price) * 0.015, 1e-9)
            signed_r = float(event.realized_r)
            if event.direction == "short":
                exit_price = float(event.fired_price) - signed_r * risk
                pnl_pct = (float(event.fired_price) - exit_price) / float(event.fired_price) * 100
            else:
                exit_price = float(event.fired_price) + signed_r * risk
                pnl_pct = (exit_price - float(event.fired_price)) / float(event.fired_price) * 100
        items.append(
            StrategyTrade(
                symbol=event.symbol,
                timeframe=event.timeframe,
                direction=event.direction,
                signal_name=event.signal_name,
                entry_time=datetime.fromtimestamp(event.fired_at_ts),
                entry_price=float(event.fired_price),
                exit_time=datetime.fromtimestamp(event.closed_at_ts) if event.closed_at_ts else None,
                exit_price=exit_price,
                outcome=event.outcome,
                realized_r=float(event.realized_r) if event.realized_r is not None else None,
                pnl_pct=pnl_pct,
            )
        )
    return StrategyTradesResponse(items=items, total=len(items))


@router.post("/{strategy_id}/refresh", status_code=status.HTTP_202_ACCEPTED)
async def refresh_strategy(
    strategy_id: str,
    timeframe: str = Query("4h"),
    db: AsyncSession = Depends(get_db),
):
    registry = _registry()
    if strategy_id not in registry.names:
        raise HTTPException(status_code=404, detail="策略不存在")
    result = await db.execute(select(Watchlist.symbol).where(Watchlist.user_id == 1))
    symbols = [row[0].upper() for row in result.all()]
    if not symbols:
        fallback = await db.execute(
            select(MarketCandle.symbol)
            .where(MarketCandle.timeframe == timeframe)
            .group_by(MarketCandle.symbol)
            .limit(20)
        )
        symbols = [row[0].upper() for row in fallback.all()]
    totals = {"symbols": 0, "combos": 0, "upserts": 0, "opportunities": 0, "signal_events": 0}
    for symbol in symbols:
        scan = await run_edge_discovery_for_pair(db, symbol, timeframe)
        for key in totals:
            totals[key] += scan.get(key, 0)
    return {"accepted": True, "strategy_id": strategy_id, **totals}
