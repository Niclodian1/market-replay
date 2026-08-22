"""
回测引擎 —— 支持 MA 双均线、RSI、MACD 策略 + 多级别交易系统。
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from typing import Optional

from app.core.indicators import _sma, _ema, _rsi as _rsi_series, _macd, _bollinger, _atr
from app.core.elder_impulse import elder_impulse
from app.core.price_action import detect_pinbar, detect_engulfing, detect_w_bottom
from app.core.sr_levels import signals_sr_breakout
# 信号原语已抽到 core/signals/primitives.py（断开循环依赖）。此处 re-export，保持
# 本模块内部(run_mtf_backtest)及 golden 测试中的 be._signals_* 引用不变。
from app.core.signals.primitives import (  # noqa: F401
    _signals_ma_cross, _signals_rsi, _signals_macd, _signals_ma_breakout,
    _signals_bollinger, _find_extremes,
)
from app.core.signals.backtest_indicators import get_backtest_signal


def _signals_macd_divergence(candles: list[dict]) -> list[int]:
    """MACD 柱极值背离（口径统一：动能极值相对减弱）。"""
    from app.core.divergence_detectors import macd_extreme_divergence_array
    return macd_extreme_divergence_array(candles)


# ─── 数据结构 ────────────────────────────────────────────────────────────────

@dataclass
class Trade:
    entry_time: int
    entry_price: float
    exit_time: int
    exit_price: float
    max_profit_pct: float = 0.0
    max_loss_pct: float = 0.0

    @property
    def pnl_pct(self) -> float:
        return (self.exit_price - self.entry_price) / self.entry_price * 100


@dataclass
class BacktestResult:
    symbol: str
    timeframe: str
    conditions: list
    start_date: str
    end_date: str
    initial_capital: float

    total_return_pct: float
    annualized_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    win_rate_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    buy_hold_return_pct: float

    equity_curve: list[dict]   # [{time, value}]
    trades: list[dict]         # serialised Trade list


# ─── 工具函数 ────────────────────────────────────────────────────────────────

def _annualized(total_pct: float, n_days: float) -> float:
    if n_days <= 0:
        return 0.0
    factor = 365.0 / n_days
    try:
        return ((1 + total_pct / 100) ** factor - 1) * 100
    except Exception:
        return 0.0


def _max_drawdown(equity: list[float]) -> float:
    peak, max_dd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        dd = (peak - v) / peak * 100
        max_dd = max(max_dd, dd)
    return max_dd


def _sharpe(returns: list[float], risk_free: float = 0.0) -> float:
    if len(returns) < 2:
        return 0.0
    n = len(returns)
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    daily_rf = risk_free / 252
    return (mean - daily_rf) / std * math.sqrt(252)


# ─── 策略信号生成 ─────────────────────────────────────────────────────────────

# 信号生成函数(_signals_ma_cross / _rsi / _macd / _ma_breakout / _bollinger /
# _macd_divergence)与 _find_extremes 已迁至 core/signals/primitives.py，并由
# core/signals/backtest_indicators.py 包成 SignalDetector；本模块在顶部 re-export。


def _filter_atr(
    highs: list[float], lows: list[float], closes: list[float],
    period: int = 14, min_pct: float = 1.0,
) -> list[int]:
    atr_vals = _atr(highs, lows, closes, period)
    return [
        1 if (atr_vals[i] is not None and closes[i] > 0 and (atr_vals[i] / closes[i]) * 100 >= min_pct)  # type: ignore[operator]
        else 0
        for i in range(len(closes))
    ]


def _filter_volume(volumes: list[float], period: int = 20, multiplier: float = 1.0) -> list[int]:
    vol_sma = _sma(volumes, period)
    return [
        1 if (vol_sma[i] is not None and volumes[i] >= vol_sma[i] * multiplier)  # type: ignore[operator]
        else 0
        for i in range(len(volumes))
    ]


# ─── 多指标 AND 组合 ──────────────────────────────────────────────────────────

def _to_state(signals: list[int]) -> list[int]:
    """将点信号转为持续状态（信号出现后保持，直到被反向信号覆盖）。
    例: [0,0,1,0,0,-1,0] → [0,0,1,1,1,-1,-1]
    """
    state, current = [], 0
    for s in signals:
        if s != 0:
            current = s
        state.append(current)
    return state


def _combined_and_signals(states_list: list[list[int]]) -> list[int]:
    """
    AND 组合逻辑：所有指标状态同时为 1 时触发买入，同时为 -1 时触发卖出。
    检测状态整体转换点：非全1 → 全1 = BUY，非全-1 → 全-1 = SELL。
    """
    n = len(states_list[0])
    signals = [0] * n
    for i in range(1, n):
        curr_all_bull = all(s[i]   == 1  for s in states_list)
        prev_all_bull = all(s[i-1] == 1  for s in states_list)
        curr_all_bear = all(s[i]   == -1 for s in states_list)
        prev_all_bear = all(s[i-1] == -1 for s in states_list)
        if curr_all_bull and not prev_all_bull:
            signals[i] = 1
        elif curr_all_bear and not prev_all_bear:
            signals[i] = -1
    return signals


def _combined_or_signals(states_list: list[list[int]]) -> list[int]:
    n = len(states_list[0])
    signals = [0] * n
    for i in range(1, n):
        curr_any_bull = any(s[i] == 1  for s in states_list)
        prev_any_bull = any(s[i-1] == 1  for s in states_list)
        curr_any_bear = any(s[i] == -1 for s in states_list)
        prev_any_bear = any(s[i-1] == -1 for s in states_list)
        if curr_any_bull and not prev_any_bull:
            signals[i] = 1
        elif curr_any_bear and not prev_any_bear:
            signals[i] = -1
    return signals


def _combined_majority_signals(states_list: list[list[int]]) -> list[int]:
    n = len(states_list[0])
    m = len(states_list)
    threshold = m / 2
    signals = [0] * n
    for i in range(1, n):
        bull_count = sum(1 for s in states_list if s[i] == 1)
        bear_count = sum(1 for s in states_list if s[i] == -1)
        prev_bull  = sum(1 for s in states_list if s[i-1] == 1)
        prev_bear  = sum(1 for s in states_list if s[i-1] == -1)
        if bull_count > threshold and prev_bull <= threshold:
            signals[i] = 1
        elif bear_count > threshold and prev_bear <= threshold:
            signals[i] = -1
    return signals


def _combine_signals(states_list: list[list[int]], mode: str) -> list[int]:
    if mode == "or":
        return _combined_or_signals(states_list)
    elif mode == "majority":
        return _combined_majority_signals(states_list)
    return _combined_and_signals(states_list)


def _apply_filters(signals: list[int], filter_masks: list[list[int]]) -> list[int]:
    if not filter_masks:
        return signals
    n = len(signals)
    result = [0] * n
    for i in range(n):
        if all(mask[i] == 1 for mask in filter_masks):
            result[i] = signals[i]
    return result


def _make_signals(strategy: str, params: dict, candles: list[dict]) -> list[int]:
    """根据策略 ID 和参数生成原始点信号列表（统一走信号注册表）。"""
    try:
        detector = get_backtest_signal(strategy)
    except KeyError:
        raise ValueError(f"未知策略: {strategy}")
    return detector.signal_array(candles, params or {})


def _make_filter(strategy: str, params: dict, candles: list[dict]) -> list[int]:
    """根据过滤器 ID 和参数生成 0/1 mask 列表。"""
    if strategy == "atr_filter":
        return _filter_atr(
            [c["high"] for c in candles], [c["low"] for c in candles], [c["close"] for c in candles],
            int(params.get("period", 14)), float(params.get("min_pct", 1.0)),
        )
    elif strategy == "volume_filter":
        return _filter_volume(
            [c["volume"] for c in candles],
            int(params.get("period", 20)), float(params.get("multiplier", 1.0)),
        )
    else:
        raise ValueError(f"未知过滤器: {strategy}")


# ─── 回测主逻辑 ───────────────────────────────────────────────────────────────

_FILTER_STRATEGIES = {"atr_filter", "volume_filter"}


def run_backtest(
    candles: list[dict],
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
    conditions: list[dict],   # [{"strategy": str, "params": dict, "role"?: str}, ...]
    initial_capital: float = 10_000.0,
    combine_mode: str = "and",
) -> BacktestResult:
    if len(candles) < 10:
        raise ValueError("历史数据不足，无法回测")
    if not conditions:
        raise ValueError("至少需要一个指标条件")

    closes = [c["close"] for c in candles]
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    times  = [c["time"]  for c in candles]

    signal_conds = [c for c in conditions if c.get("role", "signal") == "signal" and c["strategy"] not in _FILTER_STRATEGIES]
    filter_conds = [c for c in conditions if c.get("role") == "filter" or c["strategy"] in _FILTER_STRATEGIES]

    if not signal_conds:
        raise ValueError("至少需要一个信号类型的指标条件")

    states_list = [
        _to_state(_make_signals(cond["strategy"], cond.get("params", {}), candles))
        for cond in signal_conds
    ]

    filter_masks = [
        _make_filter(cond["strategy"], cond.get("params", {}), candles)
        for cond in filter_conds
    ]

    signals = _combine_signals(states_list, combine_mode)
    signals = _apply_filters(signals, filter_masks)

    # ── 模拟撮合 ───────────────────────────────────────────────────────────
    capital   = initial_capital
    position  = 0.0          # 持有股数
    entry_price = 0.0
    entry_time  = 0
    entry_idx   = 0
    trades: list[Trade] = []
    equity: list[float] = []

    for i, sig in enumerate(signals):
        price = closes[i]
        # 更新权益曲线（当前市值）
        equity.append(capital + position * price)

        if sig == 1 and position == 0.0:
            # 全仓买入
            position    = capital / price
            entry_price = price
            entry_time  = times[i]
            entry_idx   = i
            capital     = 0.0

        elif sig == -1 and position > 0.0:
            # 全部卖出
            capital  = position * price
            position = 0.0
            period_highs = highs[entry_idx : i + 1]
            period_lows  = lows[entry_idx  : i + 1]
            mfp = (max(period_highs) - entry_price) / entry_price * 100
            mfl = (min(period_lows)  - entry_price) / entry_price * 100
            trades.append(Trade(entry_time, entry_price, times[i], price, mfp, mfl))

    # 强制平仓（以最后收盘价）
    if position > 0.0:
        final_price = closes[-1]
        capital     = position * final_price
        last_idx    = len(closes) - 1
        period_highs = highs[entry_idx : last_idx + 1]
        period_lows  = lows[entry_idx  : last_idx + 1]
        mfp = (max(period_highs) - entry_price) / entry_price * 100
        mfl = (min(period_lows)  - entry_price) / entry_price * 100
        trades.append(Trade(entry_time, entry_price, times[-1], final_price, mfp, mfl))
        equity[-1]  = capital

    final_capital = capital

    # ── 统计指标 ──────────────────────────────────────────────────────────
    total_return_pct = (final_capital - initial_capital) / initial_capital * 100

    first_ts, last_ts = times[0], times[-1]
    n_days = (last_ts - first_ts) / 86400.0

    ann = _annualized(total_return_pct, n_days)

    max_dd = _max_drawdown(equity) if equity else 0.0

    daily_returns: list[float] = []
    for j in range(1, len(equity)):
        if equity[j - 1] > 0:
            daily_returns.append((equity[j] - equity[j - 1]) / equity[j - 1])

    sharpe = _sharpe(daily_returns)

    winning = [t for t in trades if t.pnl_pct >= 0]
    losing  = [t for t in trades if t.pnl_pct < 0]
    win_rate = len(winning) / len(trades) * 100 if trades else 0.0

    buy_hold = (closes[-1] - closes[0]) / closes[0] * 100 if closes[0] else 0.0

    equity_curve = [{"time": times[i], "value": round(equity[i], 4)} for i in range(len(equity))]
    trades_out   = [
        {
            "entry_time":      t.entry_time,
            "entry_price":     round(t.entry_price, 6),
            "exit_time":       t.exit_time,
            "exit_price":      round(t.exit_price, 6),
            "pnl_pct":         round(t.pnl_pct, 4),
            "max_profit_pct":  round(t.max_profit_pct, 4),
            "max_loss_pct":    round(t.max_loss_pct, 4),
        }
        for t in trades
    ]

    return BacktestResult(
        symbol=symbol,
        timeframe=timeframe,
        conditions=conditions,
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        total_return_pct=round(total_return_pct, 4),
        annualized_return_pct=round(ann, 4),
        max_drawdown_pct=round(max_dd, 4),
        sharpe_ratio=round(sharpe, 4),
        win_rate_pct=round(win_rate, 4),
        total_trades=len(trades),
        winning_trades=len(winning),
        losing_trades=len(losing),
        buy_hold_return_pct=round(buy_hold, 4),
        equity_curve=equity_curve,
        trades=trades_out,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  多级别交易系统 (MTF — Multi-Timeframe)
# ═══════════════════════════════════════════════════════════════════════════════

_TF_ORDER = {'1week': 4, '1day': 3, '4h': 2, '1h': 1}

_ENTRY_SIGNAL_NAMES = {
    'ma_breakout': '均线突破',
    'macd_divergence': 'MACD背离',
    'sr_breakout': '支撑阻力突破',
    'pinbar': 'Pinbar 锤子线',
    'engulfing': '吞没形态',
    'w_bottom': 'W底双底',
}


def _trend_direction(closes: list[float], ma_period: int = 60) -> list[int]:
    """
    价格与 MA 的位置关系:
      +1 = 价格在 MA 上方 (只做多)
      -1 = 价格在 MA 下方 (只做空)
       0 = MA 尚未形成
    """
    ma = _sma(closes, ma_period)
    result = [0] * len(closes)
    for i in range(len(closes)):
        if ma[i] is None:
            continue
        result[i] = 1 if closes[i] > ma[i] else -1
    return result


def _align_higher_tf_trend(
    higher_candles: list[dict],
    higher_trend: list[int],
    exec_timestamp: int,
) -> int:
    """二分查找: 找 <= exec_timestamp 的最近高级别 K 线的趋势方向。"""
    times = [c['time'] for c in higher_candles]
    idx = bisect.bisect_right(times, exec_timestamp) - 1
    if idx < 0:
        return 0
    return higher_trend[idx]


def _generate_entry_signals(
    candles: list[dict],
    closes: list[float],
    signal_ids: list[str],
    params: dict,
) -> dict[str, list[int]]:
    """为每种入场信号生成独立的信号序列。"""
    result: dict[str, list[int]] = {}
    for sid in signal_ids:
        if sid == 'ma_breakout':
            period = int(params.get('ma_breakout_period', 30))
            result[sid] = _signals_ma_breakout(closes, period)
        elif sid == 'macd_divergence':
            result[sid] = _signals_macd_divergence(candles)
        elif sid == 'sr_breakout':
            result[sid] = signals_sr_breakout(
                candles,
                lookback=int(params.get('sr_lookback', 5)),
                min_touches=int(params.get('sr_min_touches', 2)),
                tolerance_pct=float(params.get('sr_tolerance_pct', 0.5)),
            )
        elif sid == 'pinbar':
            result[sid] = detect_pinbar(candles)
        elif sid == 'engulfing':
            result[sid] = detect_engulfing(candles)
        elif sid == 'w_bottom':
            result[sid] = detect_w_bottom(candles)
        else:
            raise ValueError(f"未知入场信号: {sid}")
    return result


def run_mtf_backtest(
    candles_by_tf: dict[str, list[dict]],
    symbol: str,
    execution_tf: str,
    trend_tf: str | None,
    start_date: str,
    end_date: str,
    trend_ma_period: int = 60,
    use_impulse: bool = True,
    entry_signals: list[str] | None = None,
    entry_params: dict | None = None,
    initial_capital: float = 10_000.0,
    exit_rules: dict | None = None,
) -> dict:
    """
    多级别回测引擎。

    三层漏斗:
      1) 趋势过滤 — 高级别 MA 决定方向
      2) 动量过滤 — Elder Impulse 颜色
      3) 入场信号 — 任一触发开仓，多信号重合 → 权重更高

    出场规则 (exit_rules):
      - reverse_signal: 反向技术指标信号 → 平仓
      - stop_loss_pct:  固定百分比止损 (如 5.0 表示 -5% 止损)
      - breakeven:      保本出场 (浮盈达到阈值后将止损移到入场价)
      - entry_candle_low: 入场K线最低价止损
    """
    if entry_signals is None:
        entry_signals = ['ma_breakout', 'macd_divergence', 'sr_breakout', 'pinbar', 'engulfing', 'w_bottom']
    if entry_params is None:
        entry_params = {}
    if exit_rules is None:
        exit_rules = {'reverse_signal': True}

    use_reverse = exit_rules.get('reverse_signal', True)
    stop_loss_pct = exit_rules.get('stop_loss_pct', 0)
    breakeven_trigger_pct = exit_rules.get('breakeven_trigger_pct', 0)
    use_entry_candle_low = exit_rules.get('entry_candle_low', False)

    exec_candles = candles_by_tf.get(execution_tf, [])
    if len(exec_candles) < 10:
        raise ValueError(f"执行级别 {execution_tf} 数据不足 (需要 >= 10 根 K 线)")
    if not entry_signals:
        raise ValueError("至少需要一个入场信号")

    if trend_tf is not None:
        if _TF_ORDER.get(trend_tf, 0) <= _TF_ORDER.get(execution_tf, 0):
            raise ValueError(f"趋势级别 ({trend_tf}) 必须大于执行级别 ({execution_tf})")
        trend_candles = candles_by_tf.get(trend_tf, [])
        if len(trend_candles) < trend_ma_period:
            raise ValueError(f"趋势级别 {trend_tf} 数据不足 (需要 >= {trend_ma_period} 根)")
    else:
        trend_candles = []

    closes = [c['close'] for c in exec_candles]
    highs = [c['high'] for c in exec_candles]
    lows = [c['low'] for c in exec_candles]
    times = [c['time'] for c in exec_candles]
    n = len(closes)

    # ── 第一层: 趋势方向 ──────────────────────────────────────────────────
    if trend_candles:
        higher_closes = [c['close'] for c in trend_candles]
        higher_trend = _trend_direction(higher_closes, trend_ma_period)
    else:
        higher_trend = []

    # ── 第二层: Elder Impulse ────────────────────────────────────────────
    impulse_colors = elder_impulse(closes) if use_impulse else ['blue'] * n

    # ── 第三层: 入场信号 ──────────────────────────────────────────────────
    all_signals = _generate_entry_signals(exec_candles, closes, entry_signals, entry_params)

    # ── 模拟撮合 ──────────────────────────────────────────────────────────
    realized_pnl = 0.0
    per_trade_notional = initial_capital
    open_positions: list[dict] = []
    trades: list[dict] = []
    equity: list[float] = []

    def _current_equity(i: int) -> float:
        floating = 0.0
        for pos in open_positions:
            floating += pos['notional'] * ((closes[i] - pos['entry_price']) / pos['entry_price'])
        return initial_capital + realized_pnl + floating

    def _close_position(pos: dict, i: int, exit_p: float, exit_reason: str):
        nonlocal realized_pnl
        period_highs = highs[pos['entry_idx']: i + 1]
        period_lows = lows[pos['entry_idx']: i + 1]
        pnl_pct = (exit_p - pos['entry_price']) / pos['entry_price'] * 100
        mfp = (max(period_highs) - pos['entry_price']) / pos['entry_price'] * 100
        mfl = (min(period_lows) - pos['entry_price']) / pos['entry_price'] * 100
        pnl_amount = pos['notional'] * pnl_pct / 100
        realized_pnl += pnl_amount
        trades.append({
            'entry_time': pos['entry_time'],
            'entry_price': round(pos['entry_price'], 6),
            'exit_time': times[i],
            'exit_price': round(exit_p, 6),
            'pnl_pct': round(pnl_pct, 4),
            'pnl_amount': round(pnl_amount, 4),
            'max_profit_pct': round(mfp, 4),
            'max_loss_pct': round(mfl, 4),
            'signal_weight': pos['entry_weight'],
            'signal_names': pos['entry_names'],
            'direction': 'long',
            'exit_reason': exit_reason,
        })
        open_positions.remove(pos)

    for i in range(n):
        price = closes[i]

        # 获取趋势方向
        if trend_candles:
            trend_dir = _align_higher_tf_trend(trend_candles, higher_trend, times[i])
        else:
            trend_dir = 0

        # 收集当前 bar 各信号（过滤前，用于平仓判断）
        raw_long: list[str] = []
        raw_short: list[str] = []

        for sid, sig_list in all_signals.items():
            sig = sig_list[i]
            if sig == 1:
                raw_long.append(sid)
            elif sig == -1:
                raw_short.append(sid)

        # ── 出场判断：每笔持仓独立检查，允许同一时刻多笔持仓并存 ──
        for pos in open_positions[:]:
            exit_triggered = False

            # 1) 固定百分比止损: 用当根K线最低价判断是否触及止损
            if not exit_triggered and stop_loss_pct > 0:
                stop_price = pos['entry_price'] * (1 - stop_loss_pct / 100)
                if lows[i] <= stop_price:
                    _close_position(pos, i, stop_price, 'stop_loss')
                    exit_triggered = True

            # 2) 入场K线最低价止损
            if not exit_triggered and use_entry_candle_low and pos['entry_candle_low'] > 0:
                if lows[i] <= pos['entry_candle_low']:
                    _close_position(pos, i, pos['entry_candle_low'], 'entry_candle_low')
                    exit_triggered = True

            # 3) 保本出场: 浮盈达到阈值后，价格回落到入场价则平仓
            if not exit_triggered and breakeven_trigger_pct > 0:
                max_high = max(highs[pos['entry_idx']: i + 1])
                max_profit = (max_high - pos['entry_price']) / pos['entry_price'] * 100
                if max_profit >= breakeven_trigger_pct and lows[i] <= pos['entry_price']:
                    _close_position(pos, i, pos['entry_price'], 'breakeven')
                    exit_triggered = True

            # 4) 反向技术指标信号
            if not exit_triggered and use_reverse and raw_short:
                _close_position(pos, i, price, 'reverse_signal')
                exit_triggered = True

        # 过滤后的信号用于开仓
        long_signals = raw_long[:]
        short_signals = raw_short[:]

        # 趋势过滤
        if trend_dir == 1:
            short_signals = []
        elif trend_dir == -1:
            long_signals = []

        # Impulse 过滤
        color = impulse_colors[i]
        if color == 'red':
            long_signals = []
        elif color == 'green':
            short_signals = []

        # 开仓: 每个有效入场信号单独开一笔，不再因为已有持仓而跳过新机会。
        if long_signals:
            open_positions.append({
                'entry_price': price,
                'entry_time': times[i],
                'entry_idx': i,
                'entry_candle_low': lows[i],
                'entry_weight': len(long_signals),
                'entry_names': long_signals[:],
                'notional': per_trade_notional,
            })

        equity.append(_current_equity(i))

    # 强制平仓
    for pos in open_positions[:]:
        _close_position(pos, n - 1, closes[-1], 'end_of_data')
    if equity:
        equity[-1] = initial_capital + realized_pnl

    final_capital = initial_capital + realized_pnl

    # ── 统计指标 ──────────────────────────────────────────────────────────
    total_return_pct = (final_capital - initial_capital) / initial_capital * 100
    n_days = (times[-1] - times[0]) / 86400.0
    ann = _annualized(total_return_pct, n_days)
    max_dd = _max_drawdown(equity) if equity else 0.0

    daily_returns: list[float] = []
    for j in range(1, len(equity)):
        if equity[j - 1] > 0:
            daily_returns.append((equity[j] - equity[j - 1]) / equity[j - 1])
    sharpe = _sharpe(daily_returns)

    winning = [t for t in trades if t['pnl_pct'] >= 0]
    losing = [t for t in trades if t['pnl_pct'] < 0]
    win_rate = len(winning) / len(trades) * 100 if trades else 0.0
    buy_hold = (closes[-1] - closes[0]) / closes[0] * 100 if closes[0] else 0.0

    equity_curve = [{'time': times[i], 'value': round(equity[i], 4)} for i in range(len(equity))]

    candles_out = [
        {'time': c['time'], 'open': c['open'], 'high': c['high'],
         'low': c['low'], 'close': c['close'], 'volume': c['volume']}
        for c in exec_candles
    ]

    return {
        'symbol': symbol,
        'timeframe': execution_tf,
        'trend_tf': trend_tf,
        'trend_ma_period': trend_ma_period,
        'use_impulse': use_impulse,
        'entry_signals': entry_signals,
        'start_date': start_date,
        'end_date': end_date,
        'initial_capital': initial_capital,
        'total_return_pct': round(total_return_pct, 4),
        'annualized_return_pct': round(ann, 4),
        'max_drawdown_pct': round(max_dd, 4),
        'sharpe_ratio': round(sharpe, 4),
        'win_rate_pct': round(win_rate, 4),
        'total_trades': len(trades),
        'winning_trades': len(winning),
        'losing_trades': len(losing),
        'buy_hold_return_pct': round(buy_hold, 4),
        'equity_curve': equity_curve,
        'trades': trades,
        'candles': candles_out,
    }
