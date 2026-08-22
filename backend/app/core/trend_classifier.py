"""
趋势/震荡识别 —— 三维度打分 → 5 种状态。

维度 1: 摆动点结构 (HH+HL / LH+LL / 混乱) → ±2
维度 2: MA20 vs MA60 排列 → ±1
维度 3: 价格 vs MA60 位置 → ±1

总分 -4 ~ +4 → 强降/弱降/震荡/弱升/强升
"""
from __future__ import annotations


# ═══════════════════════════════════════════════════════════════════════════════
#  指标（自包含，不依赖外部文件）
# ═══════════════════════════════════════════════════════════════════════════════

def _sma(values: list[float], period: int) -> list[float | None]:
    n = len(values)
    result: list[float | None] = [None] * n
    for i in range(period - 1, n):
        result[i] = sum(values[i - period + 1: i + 1]) / period
    return result


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
#  摆动点检测
# ═══════════════════════════════════════════════════════════════════════════════

def swing_points(closes: list[float], pivot: int = 10) -> tuple[set[int], set[int]]:
    """
    找摆动高点和摆动低点。

    摆动高点: 某 bar 收盘价 > 前后各 pivot 根 bar 的收盘价
    摆动低点: 某 bar 收盘价 < 前后各 pivot 根 bar 的收盘价

    pivot=10 用于趋势识别（较宽的摆动点，过滤小波动）。
    """
    n = len(closes)
    highs: set[int] = set()
    lows: set[int] = set()
    for i in range(pivot, n - pivot):
        window_highs = closes[i - pivot: i + pivot + 1]
        window_lows = closes[i - pivot: i + pivot + 1]
        if closes[i] == max(window_highs):
            highs.add(i)
        if closes[i] == min(window_lows):
            lows.add(i)
    return highs, lows


def swing_points_tight(closes: list[float], pivot: int = 5) -> tuple[set[int], set[int]]:
    """
    摆动点检测（紧版，用于信号检测中的趋势线/通道找点）。
    pivot=5，敏感度更高，能找到更多结构点。
    """
    return swing_points(closes, pivot)


# ═══════════════════════════════════════════════════════════════════════════════
#  三维度打分
# ═══════════════════════════════════════════════════════════════════════════════

def _swing_score(closes: list[float], sw_highs: set[int], sw_lows: set[int]) -> int:
    """
    维度 1: 摆动点结构。

    取最近两个摆动高点和最近两个摆动低点，判断 HH+HL 或 LH+LL。
    """
    sorted_highs = sorted([h for h in sw_highs if h < len(closes)])
    sorted_lows = sorted([l for l in sw_lows if l < len(closes)])

    if len(sorted_highs) < 2 or len(sorted_lows) < 2:
        return 0

    h1, h2 = sorted_highs[-2], sorted_highs[-1]
    l1, l2 = sorted_lows[-2], sorted_lows[-1]

    hh = closes[h2] > closes[h1]  # higher high
    hl = closes[l2] > closes[l1]  # higher low
    lh = closes[h2] < closes[h1]  # lower high
    ll = closes[l2] < closes[l1]  # lower low

    if hh and hl:
        return 2   # 上升结构
    elif lh and ll:
        return -2  # 下降结构
    return 0       # 混乱


def _ma_score(ma20: list[float | None], ma60: list[float | None], i: int) -> int:
    """维度 2: 均线排列。"""
    if i < 60:
        return 0
    v20 = ma20[i]
    v60 = ma60[i]
    if v20 is None or v60 is None:
        return 0
    if v20 > v60:
        return 1
    elif v20 < v60:
        return -1
    return 0


def _price_position_score(closes: list[float], ma60: list[float | None], i: int) -> int:
    """维度 3: 近 5 根收盘价全部在 MA60 之上/之下。"""
    if i < 5 or i < 60:
        return 0
    above = all(
        closes[i - j] > ma60[i - j]
        for j in range(5)
        if ma60[i - j] is not None
    )
    below = all(
        closes[i - j] < ma60[i - j]
        for j in range(5)
        if ma60[i - j] is not None
    )
    # 只有全部 5 根有效才算
    valid_above = sum(1 for j in range(5) if ma60[i - j] is not None) == 5
    if not valid_above:
        return 0
    if above:
        return 1
    elif below:
        return -1
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
#  状态判定
# ═══════════════════════════════════════════════════════════════════════════════

REGIME_LABELS: dict[int, str] = {
    2: "强上升趋势",
    1: "弱上升趋势",
    0: "震荡",
    -1: "弱下降趋势",
    -2: "强下降趋势",
}


def score_to_regime(score: int) -> int:
    """总分 → 5 种状态 ID。"""
    if score >= 4:
        return 2
    elif score >= 2:
        return 1
    elif score >= -1:
        return 0
    elif score >= -3:
        return -1
    else:
        return -2


def classify_single(candles: list[dict], i: int, sw_highs: set[int], sw_lows: set[int],
                    ma20: list[float | None], ma60: list[float | None], pivot: int = 10) -> int:
    """对第 i 根 bar 做单点状态分类。

    as-of 契约：中心摆动点只有经过右侧 pivot 根（idx + pivot <= i）后才确认可用，
    不得让未来已知的 pivot 改写历史 regime。
    """
    closes = [float(c["close"]) for c in candles]
    # 只使用在 bar i 时刻已经确认的摆动点（右侧 pivot 根已走完）
    confirmed_highs = {h for h in sw_highs if h + pivot <= i}
    confirmed_lows = {l for l in sw_lows if l + pivot <= i}
    s1 = _swing_score(closes, confirmed_highs, confirmed_lows)
    s2 = _ma_score(ma20, ma60, i)
    s3 = _price_position_score(closes, ma60, i)
    return score_to_regime(s1 + s2 + s3)


def classify(candles: list[dict]) -> list[int]:
    """
    对全部 bars 做状态分类，返回等长的 regime ID 数组。

    每根 bar: 三维度打分 → 5 种状态。
    然后再做稳定性过滤: 过去 5 根 ≥ 4 根同一状态，否则为「切换中」。
    「切换中」标记为 -99，对应回测时不交易。

    M1：内部改用统一 MarketState 预计算（经 market_state_adapters），
    输出与原实现逐位一致（由 legacy parity 测试锁定）。
    """
    n = len(candles)
    if n < 61:
        return [-99] * n
    from app.core.market_state import precompute_market_states
    from app.core.market_state_adapters import market_state_to_trend_array

    states = precompute_market_states(candles)
    return market_state_to_trend_array(states)


def get_regime_label(regime_id: int) -> str:
    return REGIME_LABELS.get(regime_id, "切换中")
