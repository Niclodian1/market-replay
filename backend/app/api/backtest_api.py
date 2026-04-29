from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.backtest_engine import run_backtest, run_mtf_backtest
from app.core.candle_store import sync_candles, load_candles, get_local_range

router = APIRouter()

_VALID_STRATEGIES = {"ma_cross", "rsi", "macd", "ma_breakout", "macd_divergence", "bollinger", "atr_filter", "volume_filter"}
_VALID_COMBINE_MODES = {"and", "or", "majority"}
_VALID_ROLES = {"signal", "filter"}
_VALID_TIMEFRAMES  = {"1h", "4h", "1day", "1week"}


class BacktestCondition(BaseModel):
    strategy: str
    params: dict = Field(default_factory=dict)
    role: str = "signal"


class BacktestRequest(BaseModel):
    symbol: str
    timeframe: str = "1day"
    start_date: str                          # YYYY-MM-DD
    end_date: str                            # YYYY-MM-DD
    conditions: list[BacktestCondition]
    initial_capital: float = 10_000.0
    combine_mode: str = "and"


@router.get("/data-range")
async def get_data_range(
    symbol: str,
    timeframe: str,
    db: AsyncSession = Depends(get_db),
):
    """查询本地已缓存的 K 线日期范围。"""
    if timeframe not in _VALID_TIMEFRAMES:
        raise HTTPException(400, f"时间周期无效，支持: {sorted(_VALID_TIMEFRAMES)}")
    min_ts, max_ts = await get_local_range(symbol.upper(), timeframe, db)
    if min_ts is None:
        return {"has_data": False, "start_date": None, "end_date": None}
    return {
        "has_data":   True,
        "start_date": datetime.fromtimestamp(min_ts, tz=timezone.utc).strftime("%Y-%m-%d"),
        "end_date":   datetime.fromtimestamp(max_ts, tz=timezone.utc).strftime("%Y-%m-%d"),
    }


@router.post("")
async def run_backtest_endpoint(
    req: BacktestRequest,
    db: AsyncSession = Depends(get_db),
):
    # ── 参数校验 ──────────────────────────────────────────────────────────
    if req.timeframe not in _VALID_TIMEFRAMES:
        raise HTTPException(400, f"时间周期无效，支持: {sorted(_VALID_TIMEFRAMES)}")
    if req.initial_capital <= 0:
        raise HTTPException(400, "初始资金必须大于 0")
    if not req.conditions:
        raise HTTPException(400, "至少需要一个指标条件")
    if req.combine_mode not in _VALID_COMBINE_MODES:
        raise HTTPException(400, f"组合模式无效，支持: {sorted(_VALID_COMBINE_MODES)}")
    for cond in req.conditions:
        if cond.strategy not in _VALID_STRATEGIES:
            raise HTTPException(400, f"策略无效: {cond.strategy}，支持: {sorted(_VALID_STRATEGIES)}")
        if cond.role not in _VALID_ROLES:
            raise HTTPException(400, f"角色��效: {cond.role}，支持: {sorted(_VALID_ROLES)}")

    try:
        start_ts = int(datetime.strptime(req.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        end_ts   = int(datetime.strptime(req.end_date,   "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) + 86399
    except ValueError:
        raise HTTPException(400, "日期格式错误，请使用 YYYY-MM-DD")

    if end_ts <= start_ts:
        raise HTTPException(400, "结束日期必须晚于开始日期")

    symbol = req.symbol.strip().upper()

    # ── 增量同步本地数据 ──────────────────────────────────────────────────
    try:
        await sync_candles(symbol, req.timeframe, db)
    except Exception as e:
        raise HTTPException(502, f"同步数据失败: {e}")

    # ── 从本地加载 K 线 ───────────────────────────────────────────────────
    candles = await load_candles(symbol, req.timeframe, start_ts, end_ts, db)
    if len(candles) < 10:
        raise HTTPException(422, "指定日期范围内数据不足（少于 10 根 K 线），请扩大日期范围")

    # ── 运行回测 ──────────────────────────────────────────────────────────
    try:
        result = run_backtest(
            candles=candles,
            symbol=symbol,
            timeframe=req.timeframe,
            start_date=req.start_date,
            end_date=req.end_date,
            conditions=[{"strategy": c.strategy, "params": c.params, "role": c.role} for c in req.conditions],
            initial_capital=req.initial_capital,
            combine_mode=req.combine_mode,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))

    return {
        "symbol":                 result.symbol,
        "timeframe":              result.timeframe,
        "conditions":             [{"strategy": c.strategy, "params": c.params, "role": c.role} for c in req.conditions],
        "combine_mode":           req.combine_mode,
        "start_date":             result.start_date,
        "end_date":               result.end_date,
        "initial_capital":        result.initial_capital,
        "total_return_pct":       result.total_return_pct,
        "annualized_return_pct":  result.annualized_return_pct,
        "max_drawdown_pct":       result.max_drawdown_pct,
        "sharpe_ratio":           result.sharpe_ratio,
        "win_rate_pct":           result.win_rate_pct,
        "total_trades":           result.total_trades,
        "winning_trades":         result.winning_trades,
        "losing_trades":          result.losing_trades,
        "buy_hold_return_pct":    result.buy_hold_return_pct,
        "equity_curve":           result.equity_curve,
        "trades":                 result.trades,
    }


# ─── 多级别回测 ──────────────────────────────────────────────────────────────

_VALID_ENTRY_SIGNALS = {"ma_breakout", "macd_divergence", "sr_breakout", "pinbar", "engulfing"}
_MTF_TIMEFRAMES = {"4h", "1day", "1week"}
_TF_ORDER = {"1week": 4, "1day": 3, "4h": 2, "1h": 1}


class MtfBacktestRequest(BaseModel):
    symbol: str
    execution_tf: str = "1day"
    trend_tf: str | None = None
    start_date: str
    end_date: str
    trend_ma_period: int = 60
    use_impulse: bool = True
    entry_signals: list[str] = Field(default=["ma_breakout", "macd_divergence", "sr_breakout", "pinbar", "engulfing"])
    entry_params: dict = Field(default_factory=dict)
    exit_rules: dict = Field(default_factory=lambda: {"reverse_signal": True})
    initial_capital: float = 10_000.0


@router.post("/mtf")
async def run_mtf_backtest_endpoint(
    req: MtfBacktestRequest,
    db: AsyncSession = Depends(get_db),
):
    # ── 参数校验 ──────────────────────────────────────────────────────────
    if req.execution_tf not in _MTF_TIMEFRAMES:
        raise HTTPException(400, f"执行级别无效，支持: {sorted(_MTF_TIMEFRAMES)}")
    if req.trend_tf is not None:
        if req.trend_tf not in _MTF_TIMEFRAMES:
            raise HTTPException(400, f"趋势级别无效，支持: {sorted(_MTF_TIMEFRAMES)}")
        if _TF_ORDER.get(req.trend_tf, 0) <= _TF_ORDER.get(req.execution_tf, 0):
            raise HTTPException(400, f"趋势级别 ({req.trend_tf}) 必须大于执行级别 ({req.execution_tf})")
    if req.initial_capital <= 0:
        raise HTTPException(400, "初始资金必须大于 0")
    if not req.entry_signals:
        raise HTTPException(400, "至少需要一个入场信号")
    for sig in req.entry_signals:
        if sig not in _VALID_ENTRY_SIGNALS:
            raise HTTPException(400, f"入场信号无效: {sig}，支持: {sorted(_VALID_ENTRY_SIGNALS)}")

    try:
        start_ts = int(datetime.strptime(req.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
        end_ts = int(datetime.strptime(req.end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) + 86399
    except ValueError:
        raise HTTPException(400, "日期格式错误，请使用 YYYY-MM-DD")

    if end_ts <= start_ts:
        raise HTTPException(400, "结束日期必须晚于开始日期")

    symbol = req.symbol.strip().upper()

    # ── 同步并加载数据 ────────────────────────────────────────────────────
    timeframes_needed = [req.execution_tf]
    if req.trend_tf:
        timeframes_needed.append(req.trend_tf)

    candles_by_tf: dict[str, list[dict]] = {}
    for tf in timeframes_needed:
        try:
            await sync_candles(symbol, tf, db)
        except Exception as e:
            raise HTTPException(502, f"同步 {tf} 数据失败: {e}")
        candles_by_tf[tf] = await load_candles(symbol, tf, start_ts, end_ts, db)

    # ── 运行回测 ──────────────────────────────────────────────────────────
    try:
        result = run_mtf_backtest(
            candles_by_tf=candles_by_tf,
            symbol=symbol,
            execution_tf=req.execution_tf,
            trend_tf=req.trend_tf,
            start_date=req.start_date,
            end_date=req.end_date,
            trend_ma_period=req.trend_ma_period,
            use_impulse=req.use_impulse,
            entry_signals=req.entry_signals,
            entry_params=req.entry_params,
            exit_rules=req.exit_rules,
            initial_capital=req.initial_capital,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))

    return result
