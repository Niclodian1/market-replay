"""统一策略实验台 API —— strategy-spine 的展示层(socket ④)。

统一契约:一次"运行"产出 N 个"变体",每个变体 = stats + trades。
- 多仓策略(策略 C)→ 多个变体(C-x2 / C-x3 / C-x4);
- 单仓指标策略(ma_cross / rsi / ...)→ 单个变体。

两类策略映射到同一信封,前端"一个可筛选策略页"即可通吃。后续 A/B/3x
只需在 catalog 注册 + 接上各自的 run 分支,不再各长一条腿。
"""
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.candle_store import sync_candles, load_candles
from app.core.backtest_engine import run_backtest
from app.core.signals.backtest_indicators import backtest_registry
from app.core.strategies import strategy_c

router = APIRouter()

_C_TIMEFRAMES = {"1day", "4h", "1h"}
_INDICATOR_TIMEFRAMES = {"1h", "4h", "1day", "1week"}


# ── catalog:可筛选的策略目录 ─────────────────────────────────────────────────
def _indicator_catalog() -> list[dict]:
    out = []
    for name in backtest_registry.names:
        det = backtest_registry.get(name)
        out.append({
            "id": name,
            "name": det.display_name,
            "kind": "single_position",
            "category": det.category,
            "description": det.description,
            "params_schema": det.params_schema,
            "timeframes": sorted(_INDICATOR_TIMEFRAMES),
            "variant_axis": None,
            "tags": [det.category, "单仓"],
        })
    return out


def _strategy_c_catalog() -> dict:
    return {
        "id": "strategy_c",
        "name": "策略C · 多均线同时上穿",
        "kind": "multi_position",
        "category": "structure",
        "description": strategy_c.MultiMaCrossUpSignal.description,
        "params_schema": {"ma_pool": strategy_c.DEFAULT_MA_POOL, "variants": strategy_c.DEFAULT_VARIANTS},
        "timeframes": sorted(_C_TIMEFRAMES),
        "variant_axis": "k",
        "tags": ["均线", "多仓", "结构"],
    }


@router.get("/catalog")
def get_catalog():
    """前端筛选页据此渲染策略列表与可选维度。"""
    return {"strategies": [_strategy_c_catalog(), *_indicator_catalog()]}


# ── 取数 ─────────────────────────────────────────────────────────────────────
async def _load_full_history(symbol: str, timeframe: str, db: AsyncSession) -> list[dict]:
    try:
        await sync_candles(symbol, timeframe, db)
    except Exception as e:  # 离线/限流时退化为已有本地数据
        if not str(e):
            raise
    candles = await load_candles(symbol, timeframe, 0, int(time.time()) + 86400, db)
    return candles


def _date_str(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


# ── run 分支:策略 C ──────────────────────────────────────────────────────────
def _run_strategy_c(symbol: str, timeframe: str, candles: list[dict]) -> dict:
    result = strategy_c.run_strategy_c(candles)
    if result is None:
        raise HTTPException(422, "数据不足,无法运行策略 C")
    closes = [c["close"] for c in candles]
    ma_lines = {
        str(m): strategy_c.rolling_sma(closes, m) for m in strategy_c.DEFAULT_MA_POOL
    }
    variants = [
        {
            "id": f"x{k}",
            "label": f"C-x{k}",
            "params": {"k": k},
            "stats": block["stats"],
            "trades": block["trades"],
        }
        for k, block in sorted(result["variants"].items())
    ]
    return {
        "strategy_id": "strategy_c",
        "name": "策略C · 多均线同时上穿",
        "symbol": symbol,
        "timeframe": timeframe,
        "kind": "multi_position",
        "candles": candles,
        "overlays": {"ma_lines": ma_lines},
        "best_variant": f"x{result['best_k']}" if result["best_k"] else None,
        "variants": variants,
    }


# ── run 分支:单仓指标策略 ────────────────────────────────────────────────────
def _run_indicator(strategy_id: str, symbol: str, timeframe: str, candles: list[dict]) -> dict:
    det = backtest_registry.get(strategy_id)
    start_date, end_date = _date_str(candles[0]["time"]), _date_str(candles[-1]["time"])
    res = run_backtest(
        candles=candles, symbol=symbol, timeframe=timeframe,
        start_date=start_date, end_date=end_date,
        conditions=[{"strategy": strategy_id, "params": {}, "role": "signal"}],
    )
    variant = {
        "id": "default",
        "label": det.display_name,
        "params": det.params_schema,
        "stats": {
            "total_return": res.total_return_pct,
            "n_trades": res.total_trades,
            "win_rate": res.win_rate_pct,
            "max_drawdown": res.max_drawdown_pct,
            "sharpe": res.sharpe_ratio,
            "is_best": True,
        },
        "trades": res.trades,
    }
    return {
        "strategy_id": strategy_id,
        "name": det.display_name,
        "symbol": symbol,
        "timeframe": timeframe,
        "kind": "single_position",
        "candles": candles,
        "overlays": None,
        "best_variant": "default",
        "variants": [variant],
    }


@router.get("/run")
async def run_strategy(
    strategy_id: str = Query(...),
    symbol: str = Query(...),
    timeframe: str = Query("1day"),
    db: AsyncSession = Depends(get_db),
):
    """对某品种某周期运行某策略,返回统一信封(variants[] = stats + trades)。"""
    symbol = symbol.strip().upper()
    is_c = strategy_id == "strategy_c"
    valid_tfs = _C_TIMEFRAMES if is_c else _INDICATOR_TIMEFRAMES
    if timeframe not in valid_tfs:
        raise HTTPException(400, f"周期无效,该策略支持: {sorted(valid_tfs)}")
    if not is_c and strategy_id not in backtest_registry.names:
        raise HTTPException(404, f"未知策略: {strategy_id}")

    candles = await _load_full_history(symbol, timeframe, db)
    if len(candles) < 10:
        raise HTTPException(422, "本地数据不足,请先在图表中加载该品种数据")

    if is_c:
        return _run_strategy_c(symbol, timeframe, candles)
    return _run_indicator(strategy_id, symbol, timeframe, candles)
