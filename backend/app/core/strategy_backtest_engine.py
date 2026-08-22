"""
策略回测引擎 —— 整合语境层 + 信号层 + 生存层。

支持:
  - 三类策略（趋势/动能/反转），各带独立出场规则
  - Regime 过滤 + 方向过滤
  - 敞口分配（优先级排序）
  - 多品种、多时间框架
  - Walk-forward 分段验证

产出: 组合权益曲线 + 按策略/品种/Regime 的逐笔交易日志。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.indicators import _sma, _atr, _macd
from app.core.regime_classifier import (
    classify_regime_at,
    alignment_direction,
    direction_permitted,
    regime_permits,
)
from app.core.position_sizing import (
    AccountConfig,
    SignalOrder,
    AllocationResult,
    allocate_exposure,
    STRATEGY_RISK_BUDGET,
    EXIT_RULES,
    PRIORITY_MAP,
    build_signal_order,
)
from app.core.signals.strategy_detectors import (
    TrendStrategyDetector,
    MomentumStrategyDetector,
    ReversalStrategyDetector,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  数据结构
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TradeRecord:
    """单笔交易记录。"""
    strategy_type: str
    symbol: str
    timeframe: str
    direction: str
    entry_time: int
    entry_price: float
    exit_time: int
    exit_price: float
    exit_reason: str
    pnl_pct: float
    pnl_amount: float
    max_profit_pct: float
    max_loss_pct: float
    holding_bars: int
    regime_at_entry: int
    risk_amount: float
    position_size: float


@dataclass
class BacktestReport:
    """回测报告。"""
    symbol: str
    timeframe: str
    start_date: str
    end_date: str
    account: AccountConfig

    # 组合指标
    total_return_pct: float
    annualized_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    win_rate_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int

    # 分类指标
    by_strategy: dict[str, dict] = field(default_factory=dict)
    by_regime: dict[int, dict] = field(default_factory=dict)

    # 详情
    equity_curve: list[dict] = field(default_factory=list)
    trades: list[TradeRecord] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
#  仓位管理器
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class _Position:
    """单笔持仓状态。"""
    strategy_type: str
    symbol: str
    timeframe: str
    direction: str
    entry_time: int
    entry_price: float
    entry_idx: int
    stop_loss: float
    position_size: float
    risk_amount: float
    regime_at_entry: int

    # 追踪
    max_favorable: float = 0.0   # 最高浮盈价格（多）/ 最低浮盈价格（空）
    breakeven_activated: bool = False
    partial_exit_done: bool = False  # 反转策略的部分止盈标记

    @property
    def is_long(self) -> bool:
        return self.direction == "long"

    def unrealized_pnl_pct(self, current_price: float) -> float:
        if self.is_long:
            return (current_price - self.entry_price) / self.entry_price * 100
        return (self.entry_price - current_price) / self.entry_price * 100

    def update_max_favorable(self, price: float):
        if self.is_long:
            self.max_favorable = max(self.max_favorable, price)
        else:
            if self.max_favorable == 0.0:
                self.max_favorable = price
            self.max_favorable = min(self.max_favorable, price)


# ═══════════════════════════════════════════════════════════════════════════════
#  主引擎
# ═══════════════════════════════════════════════════════════════════════════════

class StrategyBacktester:
    """
    策略回测引擎。

    用法:
        tester = StrategyBacktester(AccountConfig())
        report = tester.run(candles, symbol, timeframe)
    """

    def __init__(self, account: AccountConfig | None = None):
        self.account = account or AccountConfig()
        self.trend_detector = TrendStrategyDetector()
        self.momentum_detector = MomentumStrategyDetector()
        self.reversal_detector = ReversalStrategyDetector()

    def run(
        self,
        candles: list[dict],
        symbol: str,
        timeframe: str,
        start_date: str = "",
        end_date: str = "",
    ) -> BacktestReport:
        """
        在单品种单时间框架上运行三类策略组合回测。
        """
        if len(candles) < 150:
            raise ValueError(f"数据不足: 需要≥150根K线, 实际{len(candles)}根")

        closes = [float(c["close"]) for c in candles]
        highs  = [float(c["high"])  for c in candles]
        lows   = [float(c["low"])   for c in candles]
        times  = [int(c["time"])    for c in candles]
        opens  = [float(c["open"])  for c in candles]
        n = len(candles)

        # 预计算指标
        ma60 = _sma(closes, 60)
        ma20 = _sma(closes, 20)
        atr14 = _atr(highs, lows, closes, 14)
        _, _, macd_hist = _macd(closes)

        # 预计算三种策略的入场信号数组
        trend_signals = self.trend_detector.signal_array(candles, {})
        momentum_signals = self.momentum_detector.signal_array(candles, {})
        reversal_signals = self.reversal_detector.signal_array(candles, {})

        # ── 预计算 regime 和 alignment（一次性，回测循环 O(1) 查表）──
        from app.core.regime_classifier import precompute_regime_arrays
        pre_regimes, pre_alignments, _ = precompute_regime_arrays(candles)

        # 模拟
        open_positions: list[_Position] = []
        realized_pnl = 0.0
        trades: list[TradeRecord] = []
        equity: list[float] = []

        for i in range(n):
            price = closes[i]
            ts = times[i]

            # ── 1. 处理已有持仓的出场（策略各自的规则） ──
            still_open: list[_Position] = []
            for pos in open_positions:
                closed = self._check_exit(
                    pos, i, candles, closes, highs, lows, ma60, ma20, atr14, macd_hist,
                )
                if closed:
                    realized_pnl += closed.pnl_amount
                    trades.append(closed)
                    # 反转策略部分止盈: _partial_trade 挂载在 pos 上
                    pt = getattr(pos, "_partial_trade", None)
                    if pt is not None:
                        realized_pnl += pt.pnl_amount
                        trades.append(pt)
                        delattr(pos, "_partial_trade")
                        still_open.append(pos)  # 剩余仓位继续持有
                else:
                    # 检查是否有部分止盈（反转策略 MA60 修复）
                    pt = getattr(pos, "_partial_trade", None)
                    if pt is not None:
                        realized_pnl += pt.pnl_amount
                        trades.append(pt)
                        delattr(pos, "_partial_trade")
                    still_open.append(pos)
            open_positions = still_open

            # ── 2. 更新持仓的最高浮盈 ──
            for pos in open_positions:
                pos.update_max_favorable(price)

            # ── 3. 检测入场信号（用预计算的 regime/alignment）──
            signal_orders = self._collect_signals_fast(
                i, trend_signals, momentum_signals, reversal_signals,
                symbol, timeframe, closes, highs, lows, atr14,
                pre_regimes, pre_alignments,
            )

            # ── 4. 敞口分配 ──
            if signal_orders:
                # 当前已用敞口
                used_exposure = sum(p.position_size for p in open_positions)
                available = self.account.max_total_exposure - used_exposure

                # 临时修改 account 的可用敞口
                alloc_account = AccountConfig(
                    initial_capital=self.account.initial_capital + realized_pnl,
                    max_leverage=self.account.max_leverage,
                    max_total_exposure=max(0, available),
                )
                alloc = allocate_exposure(signal_orders, alloc_account)

                for order in alloc.orders:
                    if not order.filled:
                        continue
                    open_positions.append(_Position(
                        strategy_type=order.strategy_type,
                        symbol=order.symbol,
                        timeframe=order.timeframe,
                        direction=order.direction,
                        entry_time=ts,
                        entry_price=price,
                        entry_idx=i,
                        stop_loss=order.stop_loss,
                        position_size=order.position_size,
                        risk_amount=order.risk_amount,
                        regime_at_entry=order.regime_id,
                    ))

            # ── 5. 记录权益 ──
            floating = 0.0
            for pos in open_positions:
                if pos.position_size > 0:
                    floating += pos.position_size * pos.unrealized_pnl_pct(price) / 100
            equity.append(self.account.initial_capital + realized_pnl + floating)

        # ── 强制平仓 ──
        final_idx = n - 1
        final_price = closes[-1]
        for pos in open_positions:
            trade = self._force_close(pos, final_idx, final_price, times[-1])
            realized_pnl += trade.pnl_amount
            trades.append(trade)
        if equity:
            equity[-1] = self.account.initial_capital + realized_pnl

        # ── 统计 ──
        return self._build_report(
            symbol, timeframe, start_date, end_date,
            trades, equity, times, closes,
        )

    # ── 入场信号收集（快速版，用预计算 regime 查表）────────────────────────

    def _collect_signals_fast(
        self,
        i: int,
        trend_arr: list[int],
        momentum_arr: list[int],
        reversal_arr: list[int],
        symbol: str,
        timeframe: str,
        closes: list[float],
        highs: list[float],
        lows: list[float],
        atr14: list[float | None],
        pre_regimes: list[int],
        pre_alignments: list[int],
    ) -> list[SignalOrder]:
        """在第 i 根 K 线上收集信号（O(1) regime 查表）。"""
        orders: list[SignalOrder] = []
        rid = pre_regimes[i] if i < len(pre_regimes) else -1
        alignment = pre_alignments[i] if i < len(pre_alignments) else 0

        if rid < 0:
            return orders

        for arr, stype in [
            (trend_arr, "trend"),
            (momentum_arr, "momentum"),
            (reversal_arr, "reversal"),
        ]:
            if i >= len(arr) or arr[i] == 0:
                continue
            direction = "long" if arr[i] == 1 else "short"

            # Regime 过滤
            allowed, caution = regime_permits(rid, stype)
            if not allowed:
                continue

            # 方向过滤
            if not direction_permitted(alignment, direction):
                continue

            # 计算止损
            price = closes[i]
            stop_loss = self._compute_stop_loss(
                stype, direction, i, None, price, atr14, closes, highs, lows,
            )

            if stop_loss <= 0:
                continue

            orders.append(build_signal_order(
                strategy_type=stype,
                symbol=symbol,
                direction=direction,
                entry_price=price,
                stop_loss=stop_loss,
                timeframe=timeframe,
                regime_id=rid,
                regime_caution=caution,
            ))

        return orders

    def _collect_signals(
        self,
        i: int,
        candles: list[dict],
        trend_arr: list[int],
        momentum_arr: list[int],
        reversal_arr: list[int],
        symbol: str,
        timeframe: str,
        closes: list[float],
        highs: list[float],
        lows: list[float],
        atr14: list[float | None],
    ) -> list[SignalOrder]:
        """在第 i 根 K 线上收集所有策略的入场信号，经 regime + 方向过滤。"""
        orders: list[SignalOrder] = []

        # 计算当前 regime
        regime = classify_regime_at(candles, i)
        alignment = alignment_direction(candles, i)

        # 逐个策略检查
        for arr, stype in [
            (trend_arr, "trend"),
            (momentum_arr, "momentum"),
            (reversal_arr, "reversal"),
        ]:
            if i >= len(arr) or arr[i] == 0:
                continue
            direction = "long" if arr[i] == 1 else "short"

            # Regime 过滤
            allowed, caution = regime_permits(regime.id, stype)
            if not allowed:
                continue

            # 方向过滤
            if not direction_permitted(alignment, direction):
                continue

            # 计算止损
            price = closes[i]
            stop_loss = self._compute_stop_loss(
                stype, direction, i, candles, price, atr14, closes, highs, lows,
            )

            if stop_loss <= 0:
                continue

            orders.append(build_signal_order(
                strategy_type=stype,
                symbol=symbol,
                direction=direction,
                entry_price=price,
                stop_loss=stop_loss,
                timeframe=timeframe,
                regime_id=regime.id,
                regime_caution=caution,
            ))

        return orders

    def _compute_stop_loss(
        self,
        strategy_type: str,
        direction: str,
        i: int,
        candles: list[dict] | None,
        price: float,
        atr14: list[float | None],
        closes: list[float],
        highs: list[float],
        lows: list[float],
    ) -> float:
        """
        按策略类型计算初始止损价。

        v2: 使用 ATR 自适应——止损距离至少 1.5×ATR。
        解决了 ETH 上因波动大、bar 低点太近而被反复止损的问题。
        """
        is_long = direction == "long"
        av = atr14[i] if i < len(atr14) and atr14[i] is not None else 0

        if strategy_type in ("trend", "momentum"):
            bar_stop = lows[i] if is_long else highs[i]
            if av > 0 and price > 0:
                # ATR 止损: 至少 1.5×ATR 的距离
                atr_stop = price - 1.5 * av if is_long else price + 1.5 * av
                # 取更宽的止损（保护更好）
                if is_long:
                    return min(bar_stop, atr_stop)  # 更低价 = 更宽止损
                else:
                    return max(bar_stop, atr_stop)  # 更高价 = 更宽止损
            return bar_stop

        elif strategy_type == "reversal":
            # 反转: 破位极值外 + 0.5 ATR（本就宽松，保持不变）
            ext_lb = 20
            start = max(0, i - ext_lb)
            if is_long:
                extreme = min(lows[start:i + 1])
                buffer = av * 0.5 if av > 0 else 0
                return extreme - buffer
            else:
                extreme = max(highs[start:i + 1])
                buffer = av * 0.5 if av > 0 else 0
                return extreme + buffer

        return 0.0

    # ── 出场检查 ────────────────────────────────────────────────────────────

    def _check_exit(
        self,
        pos: _Position,
        i: int,
        candles: list[dict],
        closes: list[float],
        highs: list[float],
        lows: list[float],
        ma60: list[float | None],
        ma20: list[float | None],
        atr14: list[float | None],
        macd_hist: list[float | None],
    ) -> TradeRecord | None:
        """检查单笔持仓是否满足出场条件。返回 TradeRecord 或 None。"""
        price = closes[i]
        is_long = pos.is_long
        strategy = pos.strategy_type
        rules = EXIT_RULES.get(strategy, {})

        # ── 公共: 固定止损 ──
        if is_long and lows[i] <= pos.stop_loss:
            return self._record_trade(pos, i, pos.stop_loss, "止损", candles)
        if not is_long and highs[i] >= pos.stop_loss:
            return self._record_trade(pos, i, pos.stop_loss, "止损", candles)

        # ── 公共: 保本 ──
        breakeven_pct = float(rules.get("breakeven_trigger_pct", 3.0))
        if not pos.breakeven_activated and pos.unrealized_pnl_pct(price) >= breakeven_pct:
            pos.breakeven_activated = True
            pos.stop_loss = pos.entry_price  # 止损移至成本价

        if pos.breakeven_activated:
            if is_long and lows[i] <= pos.stop_loss:
                return self._record_trade(pos, i, pos.stop_loss, "保本", candles)
            if not is_long and highs[i] >= pos.stop_loss:
                return self._record_trade(pos, i, pos.stop_loss, "保本", candles)

        # ── 策略专属出场 ──
        if strategy == "trend":
            return self._exit_trend(pos, i, closes, ma60, highs, lows, candles)
        elif strategy == "momentum":
            return self._exit_momentum(pos, i, closes, ma20, atr14, highs, lows, candles)
        elif strategy == "reversal":
            return self._exit_reversal(pos, i, closes, ma60, highs, lows, atr14, candles)

        return None

    def _exit_trend(
        self, pos: _Position, i: int,
        closes: list[float], ma60: list[float | None],
        highs: list[float], lows: list[float], candles: list[dict],
    ) -> TradeRecord | None:
        """趋势策略出场: 连续2根收盘跌破 MA60 + 反向结构破坏。"""
        consecutive = 2
        if i < consecutive:
            return None

        ma_val = ma60[i]
        if ma_val is None:
            return None

        # 追踪 MA60
        if pos.is_long:
            below = all(closes[i - j] < ma60[i - j]
                       for j in range(consecutive)
                       if ma60[i - j] is not None)
            if below and sum(1 for j in range(consecutive) if ma60[i - j] is not None) == consecutive:
                return self._record_trade(pos, i, closes[i], "追踪MA60", candles)
        else:
            above = all(closes[i - j] > ma60[i - j]
                       for j in range(consecutive)
                       if ma60[i - j] is not None)
            if above and sum(1 for j in range(consecutive) if ma60[i - j] is not None) == consecutive:
                return self._record_trade(pos, i, closes[i], "追踪MA60", candles)

        return None

    def _exit_momentum(
        self, pos: _Position, i: int,
        closes: list[float], ma20: list[float | None],
        atr14: list[float | None],
        highs: list[float], lows: list[float], candles: list[dict],
    ) -> TradeRecord | None:
        """动能策略出场: ATR 先放大→再收缩→收盘低于 MA20。"""
        lookback = 20
        if i < lookback + 5:
            return None

        av_now = atr14[i]
        if av_now is None:
            return None

        # ATR 峰值检测: 过去 lookback 根内的最大值
        atr_window = [atr14[j] for j in range(max(0, i - lookback), i + 1) if atr14[j] is not None]
        if not atr_window:
            return None
        atr_peak = max(atr_window)

        # 当前 ATR 是否缩到峰值的 60% 以下
        exhaustion_ratio = 0.6
        if av_now / atr_peak > exhaustion_ratio:
            return None  # 尚未衰竭

        # 收盘跌破/升破 MA20
        ma_val = ma20[i]
        if ma_val is None:
            return None

        if pos.is_long and closes[i] < ma_val:
            return self._record_trade(pos, i, closes[i], "动能衰竭", candles)
        if not pos.is_long and closes[i] > ma_val:
            return self._record_trade(pos, i, closes[i], "动能衰竭", candles)

        return None

    def _exit_reversal(
        self, pos: _Position, i: int,
        closes: list[float], ma60: list[float | None],
        highs: list[float], lows: list[float], atr14: list[float | None],
        candles: list[dict],
    ) -> TradeRecord | None:
        """反转策略出场: MA60 平 50% → 结构位全平 → 时间止损。"""
        time_stop = 15
        price = closes[i]
        ma_val = ma60[i]

        # ── 时间止损 ──
        bars_held = i - pos.entry_idx
        if bars_held >= time_stop:
            return self._record_trade(pos, i, price, "时间止损", candles)

        # ── 目标1: 回到 MA60 → 平 50%（如果还没触发） ──
        if not pos.partial_exit_done and ma_val is not None:
            reached = (
                (pos.is_long and price >= ma_val) or
                (not pos.is_long and price <= ma_val)
            )
            if reached:
                # 部分止盈: 修改仓位为原来一半，记录部分利润
                pos.partial_exit_done = True
                half_size = pos.position_size / 2
                pnl = half_size * pos.unrealized_pnl_pct(price) / 100

                # 缩减持仓
                partial_trade = TradeRecord(
                    strategy_type=pos.strategy_type,
                    symbol=pos.symbol,
                    timeframe=pos.timeframe,
                    direction=pos.direction,
                    entry_time=pos.entry_time,
                    entry_price=pos.entry_price,
                    exit_time=int(candles[i]["time"]),
                    exit_price=price,
                    exit_reason="MA60修复·部分止盈50%",
                    pnl_pct=pos.unrealized_pnl_pct(price),
                    pnl_amount=pnl,
                    max_profit_pct=(pos.max_favorable - pos.entry_price) / pos.entry_price * 100 if pos.is_long
                                   else (pos.entry_price - pos.max_favorable) / pos.entry_price * 100,
                    max_loss_pct=0.0,
                    holding_bars=bars_held,
                    regime_at_entry=pos.regime_at_entry,
                    risk_amount=pos.risk_amount / 2,
                    position_size=half_size,
                )
                pos.position_size = half_size
                pos.risk_amount -= pos.risk_amount / 2
                # 部分止盈的记录返回到 trades，但 pos 继续存在
                # 注意：这个 trade 需要被外部收集
                # 我们用一个 hack：把 partial_trade 挂到 pos 上，外部处理
                pos._partial_trade = partial_trade  # type: ignore

        return None

    # ── 强制平仓 ────────────────────────────────────────────────────────────

    def _force_close(
        self, pos: _Position, i: int, price: float, ts: int,
    ) -> TradeRecord:
        """以最后收盘价强制平仓。"""
        return self._make_trade(pos, i, price, "强制平仓", exit_time_override=ts)

    def _record_trade(
        self, pos: _Position, i: int, exit_price: float, reason: str,
        candles: list[dict],
    ) -> TradeRecord:
        exit_ts = int(candles[i]["time"]) if i < len(candles) else int(candles[-1]["time"])
        return self._make_trade(pos, i, exit_price, reason, exit_time_override=exit_ts)

    def _make_trade(
        self, pos: _Position, i: int, exit_price: float, reason: str,
        exit_time_override: int | None = None,
    ) -> TradeRecord:
        exit_ts = exit_time_override if exit_time_override is not None else int(pos.entry_time)
        is_long = pos.is_long
        pnl_pct = pos.unrealized_pnl_pct(exit_price)
        pnl_amount = pos.position_size * pnl_pct / 100

        if is_long:
            max_profit = (pos.max_favorable - pos.entry_price) / pos.entry_price * 100 if pos.max_favorable else pnl_pct
            max_loss = pnl_pct
        else:
            if pos.max_favorable and pos.max_favorable != pos.entry_price:
                max_profit = (pos.entry_price - pos.max_favorable) / pos.entry_price * 100
            else:
                max_profit = pnl_pct
            max_loss = pnl_pct

        return TradeRecord(
            strategy_type=pos.strategy_type,
            symbol=pos.symbol,
            timeframe=pos.timeframe,
            direction=pos.direction,
            entry_time=pos.entry_time,
            entry_price=round(pos.entry_price, 6),
            exit_time=exit_ts,
            exit_price=round(exit_price, 6),
            exit_reason=reason,
            pnl_pct=round(pnl_pct, 4),
            pnl_amount=round(pnl_amount, 4),
            max_profit_pct=round(max(max_profit, pnl_pct), 4),
            max_loss_pct=round(min(max_loss, pnl_pct), 4),
            holding_bars=i - pos.entry_idx,
            regime_at_entry=pos.regime_at_entry,
            risk_amount=pos.risk_amount,
            position_size=pos.position_size,
        )

    # ── 报告生成 ────────────────────────────────────────────────────────────

    def _build_report(
        self,
        symbol: str,
        timeframe: str,
        start_date: str,
        end_date: str,
        trades: list[TradeRecord],
        equity: list[float],
        times: list[int],
        closes: list[float],
    ) -> BacktestReport:
        initial = self.account.initial_capital
        final = equity[-1] if equity else initial

        total_return = (final - initial) / initial * 100
        n_days = max((times[-1] - times[0]) / 86400.0, 1)
        ann = _annualized(total_return, n_days)
        max_dd = _max_drawdown(equity)
        sharpe = _sharpe_ratio(equity)
        buy_hold = (closes[-1] - closes[0]) / closes[0] * 100

        winning = [t for t in trades if t.pnl_pct >= 0]
        losing = [t for t in trades if t.pnl_pct < 0]
        win_rate = len(winning) / len(trades) * 100 if trades else 0.0

        # 按策略分组
        by_strat: dict[str, dict] = {}
        for stype in ["trend", "momentum", "reversal"]:
            subset = [t for t in trades if t.strategy_type == stype]
            if subset:
                w = [t for t in subset if t.pnl_pct >= 0]
                by_strat[stype] = {
                    "trades": len(subset),
                    "win_rate": round(len(w) / len(subset) * 100, 1),
                    "avg_pnl_pct": round(sum(t.pnl_pct for t in subset) / len(subset), 2),
                    "total_pnl": round(sum(t.pnl_amount for t in subset), 2),
                }

        # 按 Regime 分组
        by_regime: dict[int, dict] = {}
        for regime_id in range(6):
            subset = [t for t in trades if t.regime_at_entry == regime_id]
            if subset:
                w = [t for t in subset if t.pnl_pct >= 0]
                by_regime[regime_id] = {
                    "trades": len(subset),
                    "win_rate": round(len(w) / len(subset) * 100, 1),
                    "avg_pnl_pct": round(sum(t.pnl_pct for t in subset) / len(subset), 2),
                }

        equity_curve = [{"time": times[i], "value": round(equity[i], 4)} for i in range(len(equity))]

        return BacktestReport(
            symbol=symbol,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            account=self.account,
            total_return_pct=round(total_return, 2),
            annualized_return_pct=round(ann, 2),
            max_drawdown_pct=round(max_dd, 2),
            sharpe_ratio=round(sharpe, 2),
            win_rate_pct=round(win_rate, 1),
            total_trades=len(trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            by_strategy=by_strat,
            by_regime=by_regime,
            equity_curve=equity_curve,
            trades=trades,
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  统计工具
# ═══════════════════════════════════════════════════════════════════════════════

def _annualized(total_pct: float, n_days: float) -> float:
    if n_days <= 0:
        return 0.0
    try:
        return ((1 + total_pct / 100) ** (365.0 / n_days) - 1) * 100
    except Exception:
        return 0.0


def _max_drawdown(equity: list[float]) -> float:
    if not equity:
        return 0.0
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        peak = max(peak, v)
        dd = (peak - v) / peak * 100
        max_dd = max(max_dd, dd)
    return max_dd


def _sharpe_ratio(equity: list[float]) -> float:
    if len(equity) < 2:
        return 0.0
    returns = []
    for i in range(1, len(equity)):
        if equity[i - 1] > 0:
            returns.append((equity[i] - equity[i - 1]) / equity[i - 1])
    if not returns:
        return 0.0
    n = len(returns)
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    if var == 0:
        return 0.0
    return mean / math.sqrt(var) * math.sqrt(252)


# ═══════════════════════════════════════════════════════════════════════════════
#  便捷入口
# ═══════════════════════════════════════════════════════════════════════════════

def run_backtest(
    candles: list[dict],
    symbol: str = "BTC-USD",
    timeframe: str = "1day",
    account: AccountConfig | None = None,
) -> BacktestReport:
    """运行完整的三策略组合回测。"""
    tester = StrategyBacktester(account)
    return tester.run(candles, symbol, timeframe)
