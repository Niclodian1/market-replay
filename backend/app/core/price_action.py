"""
价格行为形态检测 — Pinbar 与吞没形态。
"""
from __future__ import annotations


def detect_pinbar(candles: list[dict], ratio: float = 2.0, min_wick_pct: float = 0.02) -> list[int]:
    """
    检测 Pinbar (锤子线/射击之星)。
    返回与 candles 等长的信号列表: 1=看涨pinbar, -1=看跌pinbar, 0=无。

    看涨 pinbar: 下影线 >= 实体 * ratio，上影线 < 实体，且下影线 >= 价格 * min_wick_pct
    看跌 pinbar: 上影线 >= 实体 * ratio，下影线 < 实体，且上影线 >= 价格 * min_wick_pct
    """
    n = len(candles)
    signals = [0] * n
    for i in range(n):
        o, h, l, c = candles[i]['open'], candles[i]['high'], candles[i]['low'], candles[i]['close']
        body = abs(c - o)
        if body < 1e-10:
            continue
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l

        if lower_wick >= body * ratio and upper_wick < body and l > 0 and lower_wick / l >= min_wick_pct:
            signals[i] = 1
        elif upper_wick >= body * ratio and lower_wick < body and h > 0 and upper_wick / h >= min_wick_pct:
            signals[i] = -1

    return signals


def detect_engulfing(candles: list[dict], min_move_pct: float = 0.02) -> list[int]:
    """
    检测吞没形态。
    返回与 candles 等长的信号列表: 1=看涨吞没, -1=看跌吞没, 0=无。

    看涨吞没: 前一根阴线跌幅 >= min_move_pct，当前阳线实体收回跌幅
    看跌吞没: 前一根阳线涨幅 >= min_move_pct，当前阴线实体收回涨幅
    """
    n = len(candles)
    signals = [0] * n
    for i in range(1, n):
        prev_o, prev_c = candles[i - 1]['open'], candles[i - 1]['close']
        curr_o, curr_c = candles[i]['open'], candles[i]['close']

        prev_bearish = prev_c < prev_o
        curr_bullish = curr_c > curr_o
        prev_bullish = prev_c > prev_o
        curr_bearish = curr_c < curr_o

        if prev_bearish and curr_bullish and prev_o > 0:
            drop_pct = (prev_o - prev_c) / prev_o
            if drop_pct >= min_move_pct and curr_c >= prev_o:
                signals[i] = 1
        elif prev_bullish and curr_bearish and prev_o > 0:
            rise_pct = (prev_c - prev_o) / prev_o
            if rise_pct >= min_move_pct and curr_c <= prev_o:
                signals[i] = -1

    return signals
