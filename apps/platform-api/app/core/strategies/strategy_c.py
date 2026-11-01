"""策略 C：多均线同时上穿（C-x2 / C-x3 / C-x4）—— strategy-spine 试点。

这是"一个想法 = 一个文件"的插件模板。它把原先散落在
`scripts/backtests/backtest_ma_strategies_c.py`(靠 subprocess + 静态 JSON)
的逻辑收编进后端,分成脊柱要求的两层:

  ① 信号层 —— MultiMaCrossUpSignal(SignalDetector)：定义"什么时候入场"。
  ② 执行层 —— run_variant / run_strategy_c：多仓 + 每仓 MA 出场 + 保本 + 成本。
                这套执行模型是 backtest_engine.run_backtest(单仓满仓)所没有的，
                按既定方案【B】从已验证脚本忠实移植而来，行为由 golden 测试锁定。

入场判定(_compute_crossed_by_bar)被两层共用，避免重复。执行模型后续可被
策略 A/B、杠杆加仓(3x)复用。
"""
from typing import Any

from app.core.signal_registry import SignalHit

# ── 成本与策略参数（与原脚本逐字一致）──────────────────────────────────────────
FEE_RATE = 0.001
SLIPPAGE = 0.0005
COST = FEE_RATE + SLIPPAGE
BODY_RISE_MIN = 0.03
BREAKEVEN_TRIGGER = 0.04
MA_EXIT_PCT = 0.03

DEFAULT_MA_POOL = [30, 60, 99, 120]
DEFAULT_VARIANTS = [2, 3, 4]


# ── 工具：滚动均线（与脚本 rolling_sma 逐字一致）────────────────────────────────
def rolling_sma(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < n:
        return out
    s = sum(values[:n])
    out[n - 1] = s / n
    for i in range(n, len(values)):
        s += values[i] - values[i - n]
        out[i] = s / n
    return out


# ── 入场判定：每根 K 线上穿了均线池里的哪些均线 ───────────────────────────────
def _compute_crossed_by_bar(closes: list[float], sma_by_n: dict[int, list[float | None]],
                            ma_pool: list[int]) -> list[list[int]]:
    """每根 K 线上穿的均线集合(prev_close ≤ prev_MA 且 close > MA)。信号层与执行层共用。"""
    n = len(closes)
    out: list[list[int]] = [[] for _ in range(n)]
    for i in range(1, n):
        c, pc = closes[i], closes[i - 1]
        crossed: list[int] = []
        for m in ma_pool:
            ma, pma = sma_by_n[m][i], sma_by_n[m][i - 1]
            if ma is not None and pma is not None and pc <= pma and c > ma:
                crossed.append(m)
        out[i] = crossed
    return out


# ── 出场判定（单笔仓位，MA_exit 为该笔自有线；与脚本 _try_exit_c 逐字一致）──────
def _try_exit_c(p: dict, c: float, lo: float, ma_exit_val: float | None) -> tuple[float | None, str | None]:
    if p.get("breakeven_pending"):
        p["stop_loss"] = p["entry_close"]
        p["breakeven_set"] = True
        p["breakeven_pending"] = False

    if c > p["max_close"]:
        p["max_close"] = c
    if not p["breakeven_set"] and not p.get("breakeven_pending") and c / p["entry_close"] - 1 > BREAKEVEN_TRIGGER:
        p["breakeven_pending"] = True

    ma_exit = (ma_exit_val is not None and ma_exit_val > 0 and (ma_exit_val - c) / ma_exit_val >= MA_EXIT_PCT)
    stop_exit = lo <= p["stop_loss"]

    if stop_exit:
        return p["stop_loss"] * (1 - COST), ("保本" if p["breakeven_set"] else "止损")
    if ma_exit:
        return c * (1 - COST), "均线"
    return None, None


def _close_position_c(p: dict, i: int, eff_exit: float, reason: str,
                      trade_rets: list[float], trade_log: list[tuple]) -> float:
    ret = eff_exit / p["eff_entry"] - 1
    risk = p["eff_entry"] - p["initial_stop_loss"]
    reward = eff_exit - p["eff_entry"]
    rr = (reward / risk) if (risk > 0 and ret > 0) else None
    max_ret = p["max_close"] * (1 - COST) / p["eff_entry"] - 1
    trade_rets.append(ret)
    trade_log.append((p["entry_idx"], i, ret, reason, p["stop_loss"], rr, max_ret,
                      p["exit_ma_period"], tuple(p["crossed"])))
    return ret


# ── 统计（与脚本 summarize 逐字一致）──────────────────────────────────────────
def _summarize(n: int, realized: float, trade_rets: list[float], trade_log: list[tuple],
               curve: list[float]) -> dict | None:
    if not trade_rets:
        return None
    total_return = realized * 100
    n_trades = len(trade_rets)
    wins = [r for r in trade_rets if r >= -0.003]
    win_rate = len(wins) / n_trades * 100
    max_profit = max(trade_rets) * 100
    avg_profit = (sum(wins) / len(wins) * 100) if wins else 0.0

    peak, max_dd = curve[0], 0.0
    for v in curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd:
            max_dd = dd

    rr_values = [t[5] for t in trade_log if t[5] is not None]
    avg_rr = (sum(rr_values) / len(rr_values)) if rr_values else None

    return {
        "n": n, "total_return": total_return, "n_trades": n_trades,
        "max_drawdown": max_dd, "win_rate": win_rate,
        "max_profit": max_profit, "avg_profit": avg_profit, "avg_rr": avg_rr,
        "trade_log": trade_log,
    }


# ── 执行层：单变体回测（并行持仓；与脚本 run_variant 逐字一致）─────────────────
def run_variant(closes, opens, lows, sma_by_n, crossed_by_bar, target_k) -> dict | None:
    open_positions: list[dict] = []
    trade_rets: list[float] = []
    trade_log: list[tuple] = []
    curve: list[float] = []
    realized = 0.0
    n = len(closes)

    for i in range(n):
        c, lo = closes[i], lows[i]

        # 1) 先处理已有持仓出场（各自看自己的 MA_exit）
        still = []
        for p in open_positions:
            ma_exit_val = sma_by_n[p["exit_ma_period"]][i]
            eff_exit, reason = _try_exit_c(p, c, lo, ma_exit_val)
            if reason:
                realized += _close_position_c(p, i, eff_exit, reason, trade_rets, trade_log)
            else:
                still.append(p)
        open_positions = still

        # 2) 再判断开新仓：恰好 target_k 根上穿 + 实体涨幅 > BODY_RISE_MIN
        crossed = crossed_by_bar[i]
        if len(crossed) == target_k:
            op = opens[i]
            body_rise = (c / op - 1) if op and op > 0 else 0
            if body_rise > BODY_RISE_MIN:
                open_positions.append({
                    "eff_entry": c * (1 + COST), "entry_close": c,
                    "stop_loss": lo, "initial_stop_loss": lo,
                    "breakeven_set": False, "breakeven_pending": False,
                    "max_close": c, "entry_idx": i,
                    "exit_ma_period": min(crossed), "crossed": sorted(crossed),
                })

        unreal = sum((c * (1 - COST) / p["eff_entry"] - 1) for p in open_positions)
        curve.append(1.0 + realized + unreal)

    # 末尾强制平仓
    last_i = n - 1
    for p in open_positions:
        eff_exit = closes[-1] * (1 - COST)
        realized += _close_position_c(p, last_i, eff_exit, "强制平仓", trade_rets, trade_log)
    if curve:
        curve[-1] = 1.0 + realized

    return _summarize(target_k, realized, trade_rets, trade_log, curve)


def _variant_block(r: dict, closes: list[float], is_best: bool) -> dict:
    """把单变体结果转成前端消费的形状(与脚本 _build_variant_block 对齐，去掉时间映射)。"""
    trades_out = []
    for ei, xi, ret, reason, sl, rr, max_ret, exit_ma, crossed in r["trade_log"]:
        entry_px = closes[ei] * (1 + COST)
        exit_px = sl * (1 - COST) if reason in ("止损", "保本") else closes[xi] * (1 - COST)
        trades_out.append({
            "entry_idx": ei, "exit_idx": xi,
            "entry_price": round(entry_px, 6), "exit_price": round(exit_px, 6),
            "stop_loss": round(sl, 6),
            "pnl_pct": round(ret * 100, 4), "max_profit_pct": round(max_ret * 100, 4),
            "rr": round(rr, 4) if rr is not None else None,
            "exit_reason": reason, "exit_ma": exit_ma, "crossed": list(crossed),
        })
    return {
        "stats": {
            "total_return": round(r["total_return"], 4),
            "n_trades": r["n_trades"],
            "win_rate": round(r["win_rate"], 4),
            "max_drawdown": round(r["max_drawdown"], 4),
            "max_profit": round(r["max_profit"], 4),
            "avg_profit": round(r["avg_profit"], 4),
            "avg_rr": round(r["avg_rr"], 4) if r["avg_rr"] is not None else None,
            "is_best": is_best,
        },
        "trades": trades_out,
    }


def run_strategy_c(candles: list[dict], ma_pool: list[int] | None = None,
                   variants: list[int] | None = None) -> dict | None:
    """对一组 OHLC K 线跑策略 C 的全部变体。返回 {variants: {k: block}, best_k}。"""
    ma_pool = ma_pool or DEFAULT_MA_POOL
    variants = variants or DEFAULT_VARIANTS
    if not candles or len(candles) < max(ma_pool) + 10:
        return None

    opens = [c["open"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]

    sma_by_n = {m: rolling_sma(closes, m) for m in ma_pool}
    crossed_by_bar = _compute_crossed_by_bar(closes, sma_by_n, ma_pool)

    results = {k: run_variant(closes, opens, lows, sma_by_n, crossed_by_bar, k) for k in variants}
    valid = [(k, r) for k, r in results.items() if r]
    best_k = max(valid, key=lambda kr: kr[1]["total_return"])[0] if valid else None

    return {
        "variants": {k: _variant_block(r, closes, is_best=(k == best_k)) for k, r in results.items() if r},
        "best_k": best_k,
    }


# ── 信号层：入场探测器（复用 _compute_crossed_by_bar，单一来源）─────────────────
class MultiMaCrossUpSignal:
    """策略 C 的入场:均线池里恰好 k 根被同时上穿 + 实体涨幅 > 阈值。"""
    name = "multi_ma_cross_up"
    display_name = "多均线同时上穿"
    category = "structure"
    description = "均线池内恰好 k 根均线被同根 K 线同时上穿，且实体涨幅超过阈值。"
    logic = "对池中每条 MA 判定上穿;恰好 k 条上穿且 (close/open-1) > body_rise_min → 做多。"
    direction_support = ["long"]
    params_schema = {"ma_pool": DEFAULT_MA_POOL, "k": 2, "body_rise_min": BODY_RISE_MIN}

    def signal_array(self, candles: list[dict[str, Any]], params: dict[str, Any]) -> list[int]:
        ma_pool = params.get("ma_pool", DEFAULT_MA_POOL)
        k = int(params.get("k", 2))
        body_rise_min = float(params.get("body_rise_min", BODY_RISE_MIN))
        closes = [c["close"] for c in candles]
        opens = [c["open"] for c in candles]
        sma_by_n = {m: rolling_sma(closes, m) for m in ma_pool}
        crossed_by_bar = _compute_crossed_by_bar(closes, sma_by_n, ma_pool)
        out = [0] * len(candles)
        for i, crossed in enumerate(crossed_by_bar):
            if len(crossed) == k:
                op = opens[i]
                if op and op > 0 and (closes[i] / op - 1) > body_rise_min:
                    out[i] = 1
        return out

    def detect(self, candles: list[dict[str, Any]], timeframe: str) -> list[SignalHit]:
        arr = self.signal_array(candles, {})
        hits: list[SignalHit] = []
        for i, s in enumerate(arr):
            if s == 1:
                c = candles[i]
                hits.append(SignalHit(self.name, "long", timeframe, 0.65,
                                      int(c["time"]), float(c["close"]), {}))
        return hits
