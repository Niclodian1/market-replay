from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.confluence import evaluate_confluence
from app.core.edge_service import get_edge, get_thresholds_db, upsert_edge_stat
from app.core.recommender import explain_plan
from app.core.signal_registry import SignalHit
from app.core.signal_registry import SignalRegistry
from app.core.signals import register_indicator_signals, register_pattern_signals, register_structure_signals
from app.models.models import MarketCandle, Opportunity, SignalEvent, TradePlan


@dataclass
class ComboOutcome:
    signals: list[str]
    realized_r: float


def _build_registry() -> SignalRegistry:
    registry = SignalRegistry()
    register_indicator_signals(registry)
    register_pattern_signals(registry)
    register_structure_signals(registry)
    return registry


def _evaluate_outcome(candles: list[dict], start_index: int, direction: str, horizon: int = 12) -> float:
    entry = float(candles[start_index]["close"])
    risk = max(entry * 0.015, 1e-9)
    if direction == "short":
        stop = entry + risk
        target = entry - risk * 2
    else:
        stop = entry - risk
        target = entry + risk * 2

    for candle in candles[start_index + 1 : min(start_index + horizon + 1, len(candles))]:
        high = float(candle["high"])
        low = float(candle["low"])
        if direction == "short":
            if high >= stop:
                return -1.0
            if low <= target:
                return 2.0
        else:
            if low <= stop:
                return -1.0
            if high >= target:
                return 2.0
    exit_price = float(candles[min(start_index + horizon, len(candles) - 1)]["close"])
    raw_r = (exit_price - entry) / risk
    if direction == "short":
        raw_r = -raw_r
    return max(min(raw_r, 2.0), -1.0)


async def _load_recent_candles(db: AsyncSession, symbol: str, timeframe: str, limit: int = 500) -> list[dict]:
    result = await db.execute(
        select(MarketCandle)
        .where(MarketCandle.symbol == symbol, MarketCandle.timeframe == timeframe)
        .order_by(MarketCandle.time.desc())
        .limit(limit)
    )
    rows = list(reversed(result.scalars().all()))
    return [
        {
            "time": row.time,
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "volume": row.volume,
        }
        for row in rows
    ]


async def run_edge_discovery_once(db: AsyncSession) -> dict[str, int]:
    pairs_result = await db.execute(
        select(MarketCandle.symbol, MarketCandle.timeframe, func.count(MarketCandle.id))
        .group_by(MarketCandle.symbol, MarketCandle.timeframe)
        .having(func.count(MarketCandle.id) >= 80)
        .order_by(MarketCandle.symbol, MarketCandle.timeframe)
        .limit(50)
    )
    pairs = pairs_result.all()
    registry = _build_registry()
    upserts = 0
    opportunities = 0
    combo_count = 0
    signal_events = 0

    for symbol, timeframe, _ in pairs:
        result = await discover_pair(db, registry, symbol, timeframe)
        combo_count += result["combos"]
        upserts += result["upserts"]
        opportunities += result["opportunities"]
        signal_events += result["signal_events"]

    return {"symbols": len(pairs), "combos": combo_count, "upserts": upserts, "opportunities": opportunities, "signal_events": signal_events}


async def run_edge_discovery_for_pair(db: AsyncSession, symbol: str, timeframe: str) -> dict[str, int]:
    registry = _build_registry()
    result = await discover_pair(db, registry, symbol.upper(), timeframe)
    return {"symbols": 1 if result["candles"] >= 80 else 0, **result}


async def discover_pair(db: AsyncSession, registry: SignalRegistry, symbol: str, timeframe: str) -> dict[str, int]:
    candles = await _load_recent_candles(db, symbol, timeframe)
    if len(candles) < 80:
        return {"candles": len(candles), "combos": 0, "upserts": 0, "opportunities": 0, "signal_events": 0}

    outcomes: dict[tuple[str, tuple[str, ...]], list[ComboOutcome]] = defaultdict(list)
    for i in range(60, max(60, len(candles) - 12)):
        window = candles[: i + 1]
        hits = registry.run_all(window, timeframe)
        confluence = evaluate_confluence(hits)
        if not confluence.passed or not confluence.direction:
            continue
        signals = sorted(hit["name"] for hit in confluence.payload.get("signals", []))
        if len(signals) < 2:
            continue
        realized_r = _evaluate_outcome(candles, i, confluence.direction)
        key = (confluence.direction, tuple(signals))
        outcomes[key].append(ComboOutcome(signals=signals, realized_r=realized_r))

    combo_count = 0
    upserts = 0
    for (direction, signals_tuple), rows in outcomes.items():
        if len(rows) < 3:
            continue
        sample_size = len(rows)
        wins = sum(1 for row in rows if row.realized_r > 0)
        avg_r = sum(row.realized_r for row in rows) / sample_size
        losses = [abs(row.realized_r) for row in rows if row.realized_r <= 0]
        avg_loss = sum(losses) / len(losses) if losses else 1.0
        win_rate = wins / sample_size
        expectancy = avg_r * win_rate - avg_loss * (1 - win_rate)
        await upsert_edge_stat(
            db,
            symbol=symbol,
            timeframe=timeframe,
            signals=list(signals_tuple),
            direction=direction,
            sample_size=sample_size,
            wins=wins,
            avg_r=avg_r,
            expectancy=expectancy,
            window_start_ts=candles[0]["time"],
            window_end_ts=candles[-1]["time"],
        )
        combo_count += 1
        upserts += 1

    latest_hits = registry.run_all(candles, timeframe)
    signal_events = await _persist_latest_signal_events(db, symbol, timeframe, latest_hits)
    opportunity_created = await _create_latest_opportunity(db, registry, symbol, timeframe, candles, latest_hits)
    return {
        "candles": len(candles),
        "combos": combo_count,
        "upserts": upserts,
        "opportunities": 1 if opportunity_created else 0,
        "signal_events": signal_events,
    }


async def _persist_latest_signal_events(
    db: AsyncSession,
    symbol: str,
    timeframe: str,
    hits: list[SignalHit],
) -> int:
    inserted = 0
    for hit in hits:
        existing = await db.execute(
            select(SignalEvent).where(
                SignalEvent.symbol == symbol,
                SignalEvent.timeframe == timeframe,
                SignalEvent.signal_name == hit.name,
                SignalEvent.direction == hit.direction,
                SignalEvent.fired_at_ts == hit.fired_at_ts,
            )
        )
        if existing.scalar_one_or_none() is not None:
            continue
        db.add(
            SignalEvent(
                symbol=symbol,
                timeframe=timeframe,
                signal_name=hit.name,
                direction=hit.direction,
                fired_at_ts=hit.fired_at_ts,
                fired_price=hit.fired_price,
                payload=hit.payload,
            )
        )
        inserted += 1
    if inserted:
        await db.commit()
    return inserted


async def _create_latest_opportunity(
    db: AsyncSession,
    registry: SignalRegistry,
    symbol: str,
    timeframe: str,
    candles: list[dict],
    latest_hits: list[SignalHit] | None = None,
) -> bool:
    if len(candles) < 80:
        return False
    hits = latest_hits if latest_hits is not None else registry.run_all(candles, timeframe)
    confluence = evaluate_confluence(hits)
    if not confluence.passed or not confluence.direction or not confluence.combo_hash:
        return False

    edge = await get_edge(db, symbol, timeframe, confluence.combo_hash, confluence.direction)
    thresholds = await get_thresholds_db(db)
    if edge is None:
        return False
    if edge.sample_size < int(thresholds["min_sample"]) or edge.wilson_lower < float(thresholds["min_winrate"]):
        return False

    signals = [hit["name"] for hit in confluence.payload.get("signals", [])]
    plan_result = explain_plan(
        symbol=symbol,
        timeframe=timeframe,
        direction=confluence.direction,
        signals=signals,
        entry=float(candles[-1]["close"]),
        edge=edge,
    )
    if plan_result["score"] < float(thresholds["min_score"]):
        return False

    existing_result = await db.execute(
        select(Opportunity).where(
            Opportunity.symbol == symbol,
            Opportunity.timeframe == timeframe,
            Opportunity.combo_hash == confluence.combo_hash,
            Opportunity.direction == confluence.direction,
            Opportunity.status == "open",
        )
    )
    if existing_result.scalar_one_or_none() is not None:
        return False

    opportunity = Opportunity(
        symbol=symbol,
        timeframe=timeframe,
        direction=confluence.direction,
        combo_hash=confluence.combo_hash,
        confluence_payload=confluence.payload,
        score=plan_result["score"],
        edge_stat_id=edge.id,
        status="open",
    )
    db.add(opportunity)
    await db.flush()

    plan = plan_result["trade_plan"]
    db.add(
        TradePlan(
            opportunity_id=opportunity.id,
            entry=plan["entry"],
            stop_loss=plan["stop_loss"],
            take_profit=plan["take_profit"],
            rr_ratio=plan["rr_ratio"],
            size_r=plan["size_r"],
            rationale=plan["rationale"],
            valid_until_ts=plan["valid_until_ts"],
        )
    )
    await db.commit()
    return True
