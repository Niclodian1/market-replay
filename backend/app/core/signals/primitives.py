"""信号原语 —— 由 backtest_engine 抽出的纯信号数学函数。

抽出的目的:让回测引擎(run_backtest)与信号探测器(SignalDetector)共享
**同一份**信号计算,避免循环导入(detector 与 engine 都依赖本模块,谁都不
反向依赖谁)。函数行为与抽出前完全一致,由 tests/test_backtest_golden.py 锁定。

每个 `_signals_*` 返回与 closes 等长的点信号列表:1=买入, -1=卖出, 0=无。
"""
from app.core.indicators import _sma, _rsi as _rsi_series, _macd, _bollinger


def _signals_ma_cross(closes: list[float], fast: int, slow: int) -> list[int]:
    """返回与 closes 等长的信号列表：1=买入, -1=卖出, 0=无"""
    ma_f = _sma(closes, fast)
    ma_s = _sma(closes, slow)
    n = len(closes)
    signals = [0] * n
    for i in range(1, n):
        if ma_f[i] is None or ma_s[i] is None or ma_f[i - 1] is None or ma_s[i - 1] is None:
            continue
        prev_cross = ma_f[i - 1] - ma_s[i - 1]  # type: ignore[operator]
        curr_cross = ma_f[i] - ma_s[i]           # type: ignore[operator]
        if prev_cross <= 0 and curr_cross > 0:
            signals[i] = 1   # 金叉 → 买
        elif prev_cross >= 0 and curr_cross < 0:
            signals[i] = -1  # 死叉 → 卖
    return signals


def _signals_rsi(closes: list[float], oversold: float, overbought: float) -> list[int]:
    rsi = _rsi_series(closes, 14)
    n = len(closes)
    signals = [0] * n
    for i in range(1, n):
        if rsi[i] is None or rsi[i - 1] is None:
            continue
        if rsi[i - 1] >= oversold and rsi[i] < oversold:  # type: ignore[operator]
            signals[i] = 1   # 超卖反弹 → 买
        elif rsi[i - 1] <= overbought and rsi[i] > overbought:  # type: ignore[operator]
            signals[i] = -1  # 超买回落 → 卖
    return signals


def _signals_macd(closes: list[float]) -> list[int]:
    macd_line, sig_line, _ = _macd(closes)
    n = len(closes)
    signals = [0] * n
    for i in range(1, n):
        if macd_line[i] is None or sig_line[i] is None or macd_line[i - 1] is None or sig_line[i - 1] is None:
            continue
        prev = macd_line[i - 1] - sig_line[i - 1]  # type: ignore[operator]
        curr = macd_line[i] - sig_line[i]           # type: ignore[operator]
        if prev <= 0 and curr > 0:
            signals[i] = 1
        elif prev >= 0 and curr < 0:
            signals[i] = -1
    return signals


def _signals_ma_breakout(closes: list[float], period: int) -> list[int]:
    ma = _sma(closes, period)
    n = len(closes)
    signals = [0] * n
    for i in range(1, n):
        if ma[i] is None or ma[i - 1] is None:
            continue
        prev_above = closes[i - 1] > ma[i - 1]  # type: ignore[operator]
        curr_above = closes[i] > ma[i]           # type: ignore[operator]
        if not prev_above and curr_above:
            signals[i] = 1   # 收盘向上穿越均线 → 买
        elif prev_above and not curr_above:
            signals[i] = -1  # 收盘向下穿越均线 → 卖
    return signals


def _signals_bollinger(closes: list[float], period: int = 20, num_std: float = 2.0) -> list[int]:
    upper, _, lower = _bollinger(closes, period, num_std)
    n = len(closes)
    signals = [0] * n
    for i in range(1, n):
        if lower[i] is None or lower[i - 1] is None or upper[i] is None or upper[i - 1] is None:
            continue
        if closes[i - 1] >= lower[i - 1] and closes[i] < lower[i]:  # type: ignore[operator]
            signals[i] = 1
        elif closes[i - 1] <= upper[i - 1] and closes[i] > upper[i]:  # type: ignore[operator]
            signals[i] = -1
    return signals


def _find_extremes(series: list[float], lookback: int) -> tuple[list[bool], list[bool]]:
    """返回每个位置是否为局部峰/谷（两侧各 lookback 根 K 线确认）。"""
    n = len(series)
    is_peak   = [False] * n
    is_trough = [False] * n
    for i in range(lookback, n - lookback):
        window = series[i - lookback : i + lookback + 1]
        if series[i] == max(window):
            is_peak[i] = True
        if series[i] == min(window):
            is_trough[i] = True
    return is_peak, is_trough


def _signals_macd_divergence(closes: list[float], lookback: int) -> list[int]:
    _, _, hist = _macd(closes)
    n = len(closes)
    signals = [0] * n

    is_peak, is_trough = _find_extremes(closes, lookback)

    prev_trough_idx: int | None = None
    prev_peak_idx:   int | None = None

    for i in range(lookback, n - lookback):
        if hist[i] is None:
            continue

        if is_trough[i]:
            if prev_trough_idx is not None and hist[prev_trough_idx] is not None:
                price_lower = closes[i] < closes[prev_trough_idx]
                hist_higher = hist[i] > hist[prev_trough_idx]   # type: ignore[operator]
                if price_lower and hist_higher and i + 1 < n:
                    signals[i + 1] = 1  # 多头背离确认后下一根买入
            prev_trough_idx = i

        if is_peak[i]:
            if prev_peak_idx is not None and hist[prev_peak_idx] is not None:
                price_higher = closes[i] > closes[prev_peak_idx]
                hist_lower   = hist[i] < hist[prev_peak_idx]    # type: ignore[operator]
                if price_higher and hist_lower and i + 1 < n:
                    signals[i + 1] = -1  # 空头背离确认后下一根卖出
            prev_peak_idx = i

    return signals
