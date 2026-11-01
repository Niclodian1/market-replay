"""
支撑阻力位检测与突破信号。
基于局部极值聚类的方法识别关键价位。
"""
from __future__ import annotations



def _find_swing_points(candles: list[dict], lookback: int = 5) -> list[tuple[int, float, str]]:
    """
    找局部高/低点。返回 [(index, price, 'high'|'low'), ...]
    """
    n = len(candles)
    points: list[tuple[int, float, str]] = []
    for i in range(lookback, n - lookback):
        highs = [candles[j]['high'] for j in range(i - lookback, i + lookback + 1)]
        lows = [candles[j]['low'] for j in range(i - lookback, i + lookback + 1)]
        if candles[i]['high'] == max(highs):
            points.append((i, candles[i]['high'], 'high'))
        if candles[i]['low'] == min(lows):
            points.append((i, candles[i]['low'], 'low'))
    return points


def find_sr_levels(
    candles: list[dict],
    lookback: int = 5,
    min_touches: int = 2,
    tolerance_pct: float = 0.5,
) -> list[float]:
    """
    基于局部极值聚类识别支撑阻力位。
    tolerance_pct: 价格在该百分比范围内视为同一水平。
    min_touches: 至少被触及次数。
    """
    points = _find_swing_points(candles, lookback)
    if not points:
        return []

    prices = sorted(p[1] for p in points)
    levels: list[float] = []
    used = [False] * len(prices)

    for i, price in enumerate(prices):
        if used[i]:
            continue
        cluster = [price]
        used[i] = True
        for j in range(i + 1, len(prices)):
            if used[j]:
                continue
            if abs(prices[j] - price) / price * 100 <= tolerance_pct:
                cluster.append(prices[j])
                used[j] = True
        if len(cluster) >= min_touches:
            levels.append(sum(cluster) / len(cluster))

    return levels


def signals_sr_breakout(
    candles: list[dict],
    lookback: int = 5,
    min_touches: int = 2,
    tolerance_pct: float = 0.5,
) -> list[int]:
    """
    支撑阻力位突破信号。
    使用滚动窗口: 在每个位置用之前的数据计算 SR 水平，避免未来函数。
    返回: 1=向上突破阻力, -1=向下突破支撑, 0=无。
    """
    n = len(candles)
    signals = [0] * n

    min_bars = lookback * 2 + 20
    if n < min_bars:
        return signals

    for i in range(min_bars, n):
        window = candles[:i]
        levels = find_sr_levels(window, lookback, min_touches, tolerance_pct)
        if not levels:
            continue

        close = candles[i]['close']
        prev_close = candles[i - 1]['close']

        for level in levels:
            tol = level * tolerance_pct / 100
            if prev_close <= level + tol and close > level + tol:
                signals[i] = 1
                break
            if prev_close >= level - tol and close < level - tol:
                signals[i] = -1
                break

    return signals
