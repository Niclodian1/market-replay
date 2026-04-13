"""
纯 Python 技术指标计算（不依赖外部库）。
输入 candles 列表，返回 MA / RSI / MACD 数据点。
"""
from typing import Optional


def _sma(values: list[float], period: int) -> list[Optional[float]]:
    n = len(values)
    result: list[Optional[float]] = [None] * n
    for i in range(period - 1, n):
        result[i] = sum(values[i - period + 1 : i + 1]) / period
    return result


def _ema(values: list[float], period: int) -> list[Optional[float]]:
    n = len(values)
    result: list[Optional[float]] = [None] * n
    if n < period:
        return result
    k = 2.0 / (period + 1)
    result[period - 1] = sum(values[:period]) / period
    for i in range(period, n):
        result[i] = values[i] * k + result[i - 1] * (1 - k)  # type: ignore[operator]
    return result


def _rsi(values: list[float], period: int = 14) -> list[Optional[float]]:
    n = len(values)
    result: list[Optional[float]] = [None] * n
    if n < period + 1:
        return result

    changes = [values[i] - values[i - 1] for i in range(1, n)]
    gains = [max(c, 0.0) for c in changes]
    losses = [max(-c, 0.0) for c in changes]

    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period

    def _rsi_val(ag: float, al: float) -> float:
        return 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)

    result[period] = _rsi_val(avg_g, avg_l)
    for i in range(period + 1, n):
        avg_g = (avg_g * (period - 1) + gains[i - 1]) / period
        avg_l = (avg_l * (period - 1) + losses[i - 1]) / period
        result[i] = _rsi_val(avg_g, avg_l)
    return result


def _macd(
    values: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[list[Optional[float]], list[Optional[float]], list[Optional[float]]]:
    n = len(values)
    ema_f = _ema(values, fast)
    ema_s = _ema(values, slow)

    macd_line: list[Optional[float]] = [
        (ema_f[i] - ema_s[i]) if (ema_f[i] is not None and ema_s[i] is not None) else None
        for i in range(n)
    ]

    # 在有效 MACD 值上计算 EMA(signal)
    first = next((i for i, v in enumerate(macd_line) if v is not None), None)
    if first is None:
        return macd_line, [None] * n, [None] * n

    valid = [v for v in macd_line if v is not None]
    sig_valid = _ema(valid, signal)

    sig_line: list[Optional[float]] = [None] * n
    for j, i in enumerate(range(first, n)):
        sig_line[i] = sig_valid[j]

    hist: list[Optional[float]] = [
        (macd_line[i] - sig_line[i])  # type: ignore[operator]
        if (macd_line[i] is not None and sig_line[i] is not None)
        else None
        for i in range(n)
    ]
    return macd_line, sig_line, hist


def calculate_indicators(candles: list[dict]) -> dict:
    """
    输入 candles（已按时间升序排列），返回各指标数据点列表。
    过滤掉 None 值，只返回有效数据。
    """
    if not candles:
        return {"ma20": [], "ma60": [], "rsi": [], "macd": []}

    times = [c["time"] for c in candles]
    closes = [c["close"] for c in candles]

    ma20_vals = _sma(closes, 20)
    ma60_vals = _sma(closes, 60)
    rsi_vals = _rsi(closes, 14)
    macd_v, sig_v, hist_v = _macd(closes)

    def to_pts(vals: list[Optional[float]]) -> list[dict]:
        return [
            {"time": times[i], "value": round(v, 6)}
            for i, v in enumerate(vals)
            if v is not None
        ]

    macd_pts = [
        {
            "time": times[i],
            "macd": round(macd_v[i], 6),      # type: ignore[arg-type]
            "signal": round(sig_v[i], 6),      # type: ignore[arg-type]
            "histogram": round(hist_v[i], 6),  # type: ignore[arg-type]
        }
        for i in range(len(times))
        if macd_v[i] is not None and sig_v[i] is not None and hist_v[i] is not None
    ]

    return {
        "ma20": to_pts(ma20_vals),
        "ma60": to_pts(ma60_vals),
        "rsi": to_pts(rsi_vals),
        "macd": macd_pts,
    }
