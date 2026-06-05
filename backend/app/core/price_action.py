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


def detect_w_bottom(
    candles: list[dict],
    min_distance: int = 60,
    swing_window: int = 30,
    tolerance_pct: float = 0.03,
    min_rally_pct: float = 0.05,
) -> list[int]:
    """
    检测 W底（双底）与 M顶（双顶）形态。
    返回与 candles 等长的信号列表: 1=W底确认, -1=M顶确认, 0=无。

    支撑位 = min(open, close) — 取实体下沿，保守取值过滤影线噪音。
    滑动窗口 = 前后各30根K线寻找极值点。
    两底间距 >= 60 根K线，支撑位差异 <= tolerance_pct。
    """
    n = len(candles)
    signals = [0] * n
    if n < swing_window * 2 + min_distance:
        return signals

    # 提取价格数组
    lows = [c['low'] for c in candles]
    highs = [c['high'] for c in candles]
    opens = [c['open'] for c in candles]
    closes = [c['close'] for c in candles]

    # 1. 寻找摆动低点
    swing_lows: list[int] = []
    for i in range(swing_window, n - swing_window):
        cur_low = lows[i]
        if all(cur_low < lows[j] for j in range(i - swing_window, i + swing_window + 1) if j != i):
            swing_lows.append(i)

    # 2. 寻找摆动高点
    swing_highs: list[int] = []
    for i in range(swing_window, n - swing_window):
        cur_high = highs[i]
        if all(cur_high > highs[j] for j in range(i - swing_window, i + swing_window + 1) if j != i):
            swing_highs.append(i)

    # 3. 匹配 W底：两低点间距足够，支撑位接近，中间有足够反弹
    for a in range(len(swing_lows)):
        i = swing_lows[a]
        support_i = min(opens[i], closes[i])
        if support_i <= 0:
            continue
        for b in range(a + 1, len(swing_lows)):
            j = swing_lows[b]
            if j - i < min_distance:
                continue
            support_j = min(opens[j], closes[j])
            if support_j <= 0:
                continue
            # 支撑位差异
            diff = abs(support_j - support_i) / support_i
            if diff > tolerance_pct:
                continue
            # 后低点不能明显下破前低支撑位
            if lows[j] < support_i * (1 - tolerance_pct / 2):
                continue
            # 中间最高点反弹幅度
            mid_high = max(highs[i + 1:j])
            rally = (mid_high - max(support_i, support_j)) / max(support_i, support_j)
            if rally < min_rally_pct:
                continue
            signals[j] = 1

    # 4. 匹配 M顶：两高点间距足够，阻力位接近，中间有足够回调
    for a in range(len(swing_highs)):
        i = swing_highs[a]
        resist_i = max(opens[i], closes[i])
        if resist_i <= 0:
            continue
        for b in range(a + 1, len(swing_highs)):
            j = swing_highs[b]
            if j - i < min_distance:
                continue
            resist_j = max(opens[j], closes[j])
            if resist_j <= 0:
                continue
            diff = abs(resist_j - resist_i) / resist_i
            if diff > tolerance_pct:
                continue
            # 后高点不能明显上破前高阻力位
            if highs[j] > resist_i * (1 + tolerance_pct / 2):
                continue
            # 中间最低点回调幅度
            mid_low = min(lows[i + 1:j])
            pullback = (min(resist_i, resist_j) - mid_low) / min(resist_i, resist_j)
            if pullback < min_rally_pct:
                continue
            signals[j] = -1

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
