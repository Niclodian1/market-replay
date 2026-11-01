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


def _bollinger(
    values: list[float], period: int = 20, num_std: float = 2.0
) -> tuple[list[Optional[float]], list[Optional[float]], list[Optional[float]]]:
    n = len(values)
    middle = _sma(values, period)
    upper: list[Optional[float]] = [None] * n
    lower: list[Optional[float]] = [None] * n
    for i in range(period - 1, n):
        window = values[i - period + 1 : i + 1]
        mean = middle[i]
        variance = sum((v - mean) ** 2 for v in window) / period  # type: ignore[operator]
        std = variance ** 0.5
        upper[i] = mean + num_std * std  # type: ignore[operator]
        lower[i] = mean - num_std * std  # type: ignore[operator]
    return upper, middle, lower


def _atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> list[Optional[float]]:
    n = len(highs)
    result: list[Optional[float]] = [None] * n
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


# ── 市场状态分类指标 ────────────────────────────────────────────────────────

def _adx(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> list[Optional[float]]:
    """
    Average Directional Index —— 趋势强度（不判方向）。

    返回与输入等长的 ADX 序列。
    典型阈值:
      ADX < 20  → 震荡 (Ranging)
      ADX 20-35 → 弱趋势 (Weak Trend)
      ADX > 35  → 强趋势 (Strong Trend)

    Wilder 平滑: next = (prev * (period - 1) + current) / period
    """
    n = len(highs)
    result: list[Optional[float]] = [None] * n
    if n < period * 2:
        return result

    # Step 1: True Range
    tr = [0.0] * n
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    # Step 2: 方向运动 +DM / -DM
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        if up > down and up > 0:
            plus_dm[i] = up
        if down > up and down > 0:
            minus_dm[i] = down

    # Step 3: Wilder 平滑 TR / +DM / -DM，得到 +DI / -DI
    # 初始值: 前 period 根的简单平均
    plus_di: list[Optional[float]] = [None] * n
    minus_di: list[Optional[float]] = [None] * n

    tr_smoothed = sum(tr[1:period + 1]) / period
    pdm_smoothed = sum(plus_dm[1:period + 1]) / period
    ndm_smoothed = sum(minus_dm[1:period + 1]) / period

    for i in range(period, n):
        if tr_smoothed == 0:
            plus_di[i] = 0.0
            minus_di[i] = 0.0
        else:
            plus_di[i] = 100.0 * pdm_smoothed / tr_smoothed
            minus_di[i] = 100.0 * ndm_smoothed / tr_smoothed

        # Wilder smoothing 递推
        tr_smoothed = (tr_smoothed * (period - 1) + tr[i]) / period
        pdm_smoothed = (pdm_smoothed * (period - 1) + plus_dm[i]) / period
        ndm_smoothed = (ndm_smoothed * (period - 1) + minus_dm[i]) / period

    # Step 4: DX = |+DI - -DI| / (+DI + -DI) * 100
    dx: list[Optional[float]] = [None] * n
    for i in range(period, n):
        if plus_di[i] is not None and minus_di[i] is not None:
            denom = plus_di[i] + minus_di[i]
            dx[i] = 100.0 * abs(plus_di[i] - minus_di[i]) / denom if denom > 0 else 0.0

    # Step 5: ADX = Wilder 平滑 DX
    # DX 有效起始位 = period ~ period*2
    valid_dx = [v for v in dx[period:period * 2] if v is not None]
    if not valid_dx:
        return result

    adx_smoothed = sum(valid_dx) / len(valid_dx)
    result[period * 2 - 1] = adx_smoothed
    for i in range(period * 2, n):
        if dx[i] is not None:
            adx_smoothed = (adx_smoothed * (period - 1) + dx[i]) / period
            result[i] = adx_smoothed

    return result


def _atr_percentile(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    atr_period: int = 14,
    lookback: int = 100,
) -> list[Optional[float]]:
    """
    ATR/Price 在 lookback 窗口内的百分位 (0.0-1.0)。

    0.0 = 窗口内最低波动，1.0 = 窗口内最高波动。
    用相对历史自身的百分位而非绝对值，因为 BTC 2021 年的"低波"
    绝对值可能比 2023 年的"高波"还高——百分位消除了这个漂移。
    """
    atr_vals = _atr(highs, lows, closes, atr_period)
    n = len(closes)
    result: list[Optional[float]] = [None] * n

    for i in range(lookback, n):
        window_start = max(0, i - lookback + 1)
        ratios: list[float] = []
        for j in range(window_start, i + 1):
            av = atr_vals[j]
            c = closes[j]
            if av is not None and c > 0:
                ratios.append(av / c)

        current_av = atr_vals[i]
        current_c = closes[i]
        if current_av is None or current_c <= 0:
            continue

        current_ratio = current_av / current_c
        # 计数严格小于 current 的比例
        rank = sum(1 for r in ratios if r < current_ratio)
        result[i] = rank / max(len(ratios), 1)

    return result


def _ma_alignment(
    closes: list[float],
    periods: tuple[int, int, int] = (20, 60, 120),
) -> list[int]:
    """
    均线排列状态。

      +2 = 价格 > MA快 > MA中 > MA慢 (完美多头排列)
      +1 = 价格 > MA快 > MA中 (基本多头)
       0 = 缠绕/无序
      -1 = 价格 < MA快 < MA中 (基本空头)
      -2 = 价格 < MA快 < MA中 < MA慢 (完美空头排列)
    """
    fast_p, mid_p, slow_p = periods
    mas = {
        "fast": _sma(closes, fast_p),
        "mid": _sma(closes, mid_p),
        "slow": _sma(closes, slow_p),
    }
    n = len(closes)
    result = [0] * n

    for i in range(slow_p - 1, n):
        f = mas["fast"][i]
        m = mas["mid"][i]
        s = mas["slow"][i]
        if any(v is None for v in (f, m, s)):
            continue
        price = closes[i]
        if price > f > m > s:
            result[i] = 2
        elif price > f > m:
            result[i] = 1
        elif price < f < m < s:
            result[i] = -2
        elif price < f < m:
            result[i] = -1

    return result


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
