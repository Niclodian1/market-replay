"""
矩阵回测引擎。

对每个信号 × 每种趋势状态，独立回测每一笔交易：
  入场: bar[i] 收盘信号 → bar[i+1] 开盘价入场
  仓位: 固定 $1000，互不干扰
  止损: 入场 bar 最低点(多) / 最高点(空)
  保本: 浮盈 > 2% → 止损移至成本价
  追踪: 从极值回撤 3×ATR → 出场
  时间: 无限制

输出: 信号 × 趋势状态 矩阵统计 + 全部交易明细
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════════════════════════
#  基础指标
# ═══════════════════════════════════════════════════════════════════════════════

def _atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float | None]:
    n = len(highs)
    result: list[float | None] = [None] * n
    if n < 2:
        return result
    tr = [highs[0] - lows[0]]
    for i in range(1, n):
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    if n < period:
        return result
    avg = sum(tr[:period]) / period
    result[period - 1] = avg
    for i in range(period, n):
        avg = (avg * (period - 1) + tr[i]) / period
        result[i] = avg
    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  数据结构
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Trade:
    signal: str
    direction: str  # "long" | "short"
    entry_time: int
    entry_price: float
    exit_time: int
    exit_price: float
    exit_reason: str  # "止损" | "保本" | "追踪" | "强制平仓"
    pnl_pct: float
    pnl_amount: float
    holding_bars: int
    regime_at_entry: int
    max_favorable_pct: float


@dataclass
class CellStats:
    """矩阵中一个格子的统计。"""
    trades: int = 0
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    profit_factor: float = 0.0  # 总盈利/总亏损
    max_win: float = 0.0
    max_loss: float = 0.0
    max_consecutive_losses: int = 0
    avg_bars: float = 0.0


@dataclass
class MatrixReport:
    symbol: str = ""
    timeframe: str = ""
    signal_names: list[str] = field(default_factory=list)
    regime_labels: dict[int, str] = field(default_factory=dict)
    matrix: dict[str, dict[int, CellStats]] = field(default_factory=dict)
    all_trades: list[Trade] = field(default_factory=list)


REGIME_LABELS = {
    2: "强上升趋势",
    1: "弱上升趋势",
    0: "震荡",
    -1: "弱下降趋势",
    -2: "强下降趋势",
}


# ═══════════════════════════════════════════════════════════════════════════════
#  回测引擎
# ═══════════════════════════════════════════════════════════════════════════════

class MatrixBacktester:
    """
    矩阵回测。用法:

        tester = MatrixBacktester()
        report = tester.run(candles, signals_map, regimes)
    """

    def run(
        self,
        candles: list[dict],
        signals_map: dict[str, list[int]],
        regimes: list[int],
        symbol: str = "",
        timeframe: str = "",
    ) -> MatrixReport:
        n = len(candles)
        if n < 120:
            raise ValueError(f"数据不足: 需要≥120根K线, 实际{n}根")

        closes = [float(c["close"]) for c in candles]
        highs = [float(c["high"]) for c in candles]
        lows = [float(c["low"]) for c in candles]
        opens = [float(c["open"]) for c in candles]
        times = [int(c["time"]) for c in candles]
        atr14 = _atr(highs, lows, closes, 14)

        all_trades: list[Trade] = []
        signal_names = list(signals_map.keys())

        for sig_name in signal_names:
            sig_arr = signals_map[sig_name]
            for i in range(n - 1):
                if sig_arr[i] == 0:
                    continue

                # 只交易 regime 稳定的 bar
                if regimes[i] == -99:
                    continue

                direction = "long" if sig_arr[i] == 1 else "short"
                trade = self._simulate_trade(
                    direction, i, candles, closes, highs, lows, opens, times, atr14,
                    sig_name, regimes[i],
                )
                if trade:
                    all_trades.append(trade)

        # 构建矩阵
        matrix = self._build_matrix(all_trades, signal_names)

        return MatrixReport(
            symbol=symbol,
            timeframe=timeframe,
            signal_names=signal_names,
            regime_labels=REGIME_LABELS,
            matrix=matrix,
            all_trades=all_trades,
        )

    def _simulate_trade(
        self,
        direction: str,
        entry_bar: int,
        candles: list[dict],
        closes: list[float],
        highs: list[float],
        lows: list[float],
        opens: list[float],
        times: list[int],
        atr14: list[float | None],
        signal_name: str,
        regime: int,
    ) -> Trade | None:
        """从 entry_bar 的信号开始，模拟一笔交易直到出场。"""
        n = len(closes)
        is_long = direction == "long"

        # 入场: bar[i+1] 的开盘价（最晚次日开盘）
        entry_i = entry_bar + 1
        if entry_i >= n:
            return None
        entry_price = opens[entry_i]

        # 结构止损: 入场 bar 的极值
        if is_long:
            stop_loss = lows[entry_bar]
        else:
            stop_loss = highs[entry_bar]

        if stop_loss <= 0 or entry_price <= 0:
            return None

        breakeven_activated = False
        max_favorable = entry_price  # 多: 最高价, 空: 最低价

        for i in range(entry_i + 1, n):
            price = closes[i]
            atr_val = atr14[i] if i < len(atr14) and atr14[i] is not None else 0

            # 更新最有利价格
            if is_long:
                max_favorable = max(max_favorable, highs[i])
            else:
                if max_favorable == entry_price:
                    max_favorable = lows[i]
                max_favorable = min(max_favorable, lows[i])

            # — 保本 —
            if not breakeven_activated:
                pnl = (price - entry_price) / entry_price if is_long else (entry_price - price) / entry_price
                if pnl > 0.02:
                    stop_loss = entry_price
                    breakeven_activated = True

            # — 追踪止损（3×ATR 从极值回撤）—
            if atr_val > 0:
                if is_long:
                    trail_stop = max_favorable - 3 * atr_val
                    if breakeven_activated or trail_stop > stop_loss:
                        stop_loss = max(stop_loss, trail_stop)
                else:
                    trail_stop = max_favorable + 3 * atr_val
                    if breakeven_activated or trail_stop < stop_loss:
                        stop_loss = min(stop_loss, trail_stop)

            # — 检查出场 —
            if is_long and lows[i] <= stop_loss:
                exit_price = stop_loss
                reason = "保本" if breakeven_activated and stop_loss >= entry_price else "追踪" if trail_stop_was_used(stop_loss, atr_val, max_favorable, is_long, entry_price) else "止损"
                if stop_loss <= lows[entry_bar]:
                    reason = "止损"
                elif breakeven_activated and abs(stop_loss - entry_price) / entry_price < 0.001:
                    reason = "保本"
                else:
                    reason = "追踪"
                return self._make_trade(
                    signal_name, direction, entry_price, exit_price, reason,
                    times[entry_i], times[i], i - entry_i, regime,
                    max_favorable, entry_price,
                )

            if not is_long and highs[i] >= stop_loss:
                exit_price = stop_loss
                if stop_loss >= highs[entry_bar]:
                    reason = "止损"
                elif breakeven_activated and abs(stop_loss - entry_price) / entry_price < 0.001:
                    reason = "保本"
                else:
                    reason = "追踪"
                return self._make_trade(
                    signal_name, direction, entry_price, exit_price, reason,
                    times[entry_i], times[i], i - entry_i, regime,
                    max_favorable, entry_price,
                )

        # — 到达末尾，强制平仓 —
        return self._make_trade(
            signal_name, direction, entry_price, closes[-1], "强制平仓",
            times[entry_i], times[-1], n - 1 - entry_i, regime,
            max_favorable, entry_price,
        )

    def _make_trade(
        self, signal: str, direction: str, entry_price: float, exit_price: float,
        reason: str, entry_time: int, exit_time: int, holding_bars: int,
        regime: int, max_favorable: float, ep: float,
    ) -> Trade:
        if direction == "long":
            pnl_pct = (exit_price - entry_price) / entry_price * 100
            max_fav_pct = (max_favorable - entry_price) / entry_price * 100
        else:
            pnl_pct = (entry_price - exit_price) / entry_price * 100
            max_fav_pct = (entry_price - max_favorable) / entry_price * 100

        pnl_amount = 1000.0 * pnl_pct / 100

        return Trade(
            signal=signal,
            direction=direction,
            entry_time=entry_time,
            entry_price=round(entry_price, 6),
            exit_time=exit_time,
            exit_price=round(exit_price, 6),
            exit_reason=reason,
            pnl_pct=round(pnl_pct, 4),
            pnl_amount=round(pnl_amount, 2),
            holding_bars=holding_bars,
            regime_at_entry=regime,
            max_favorable_pct=round(max_fav_pct, 4),
        )

    def _build_matrix(
        self, trades: list[Trade], signal_names: list[str],
    ) -> dict[str, dict[int, CellStats]]:
        """构建 signal × regime 矩阵。"""
        matrix: dict[str, dict[int, CellStats]] = {
            sig: {r: CellStats() for r in REGIME_LABELS} for sig in signal_names
        }

        # 按 signal × regime 分组
        groups: dict[tuple[str, int], list[Trade]] = {}
        for t in trades:
            key = (t.signal, t.regime_at_entry)
            groups.setdefault(key, []).append(t)

        for (sig, regime), group in groups.items():
            if sig not in matrix:
                continue
            n = len(group)
            wins = [t for t in group if t.pnl_pct >= 0]
            losses = [t for t in group if t.pnl_pct < 0]
            wr = len(wins) / n * 100 if n > 0 else 0
            avg_pnl = sum(t.pnl_pct for t in group) / n if n > 0 else 0
            total_win = sum(t.pnl_amount for t in wins)
            total_loss = abs(sum(t.pnl_amount for t in losses))
            pf = total_win / total_loss if total_loss > 0 else 0
            max_w = max((t.pnl_pct for t in group), default=0)
            max_l = min((t.pnl_pct for t in group), default=0)
            avg_bars = sum(t.holding_bars for t in group) / n if n > 0 else 0

            # 最大连续亏损
            max_cl = 0
            cur_cl = 0
            for t in group:
                if t.pnl_pct < 0:
                    cur_cl += 1
                    max_cl = max(max_cl, cur_cl)
                else:
                    cur_cl = 0

            matrix[sig][regime] = CellStats(
                trades=n,
                win_rate=round(wr, 1),
                avg_pnl=round(avg_pnl, 2),
                profit_factor=round(pf, 2),
                max_win=round(max_w, 2),
                max_loss=round(max_l, 2),
                max_consecutive_losses=max_cl,
                avg_bars=round(avg_bars, 1),
            )

        return matrix


def trail_stop_was_used(stop: float, atr_val: float, max_fav: float, is_long: bool, entry: float) -> bool:
    """判断出场是否由追踪止损触发（而非初始止损或保本）。"""
    if atr_val <= 0:
        return False
    trail = max_fav - 3 * atr_val if is_long else max_fav + 3 * atr_val
    if is_long:
        return abs(stop - trail) / trail < 0.01 and stop > entry
    else:
        return abs(stop - trail) / trail < 0.01 and stop < entry
