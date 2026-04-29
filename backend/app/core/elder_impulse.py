"""
Elder Impulse System — EMA(13) 方向 + MACD-Histogram 方向判断动量颜色。
"""
from __future__ import annotations

from app.core.indicators import _ema, _macd


def elder_impulse(closes: list[float]) -> list[str]:
    """
    返回每根 K 线的 Elder Impulse 颜色列表，长度与 closes 相同。
      'green' — EMA13 上升 AND MACD-H 上升 (强多头动量)
      'red'   — EMA13 下降 AND MACD-H 下降 (强空头动量)
      'blue'  — 其他 (中性)
    """
    n = len(closes)
    if n < 2:
        return ['blue'] * n

    ema13 = _ema(closes, 13)
    _, _, hist = _macd(closes)

    colors: list[str] = ['blue'] * n
    for i in range(1, n):
        if ema13[i] is None or ema13[i - 1] is None or hist[i] is None or hist[i - 1] is None:
            continue
        ema_rising = ema13[i] > ema13[i - 1]
        ema_falling = ema13[i] < ema13[i - 1]
        hist_rising = hist[i] > hist[i - 1]
        hist_falling = hist[i] < hist[i - 1]

        if ema_rising and hist_rising:
            colors[i] = 'green'
        elif ema_falling and hist_falling:
            colors[i] = 'red'

    return colors
