"""MarketState → 旧系统兼容适配器（M1）。

三个旧入口（regime_classifier 六态、trend_classifier 五态、日报 stage）通过
本模块映射到统一 MarketState，保留旧 ID、标签与文字。适配器只做**兼容**：
若旧系统之间互相矛盾，canonical MarketState 给出自己的唯一结论，适配器
负责映射旧口径，不把冲突藏进核心模型（见 docs/architecture/MARKET_STATE_V1.md）。

所有映射函数均为纯函数；as-of 契约由 MarketState 保证。
"""
from __future__ import annotations

from collections import Counter

from app.core.market_state import MarketState


# ═══════════════════════════════════════════════════════════════════════════════
#  → 旧 Regime 六态（ADX 档 × ATR/price 百分位档）
# ═══════════════════════════════════════════════════════════════════════════════
# 注意：旧六态用 0.5 切分波动百分位（ATR_PCT_SPLIT），与 MarketState 的
# vol_level 阈值（0.30/0.70）无关；此处为兼容旧口径，不修改核心模型。

_ADX_RANGING = 20.0      # 与 regime_classifier.ADX_RANGING 一致
_ADX_STRONG = 35.0       # 与 regime_classifier.ADX_STRONG 一致
_ATR_PCT_SPLIT = 0.5     # 与 regime_classifier.ATR_PCT_SPLIT 一致（旧六态切分）


def regime_components_from_state(ms: MarketState) -> tuple[int, int] | None:
    """返回旧 Regime 矩阵坐标 (t, v)：t=ADX 档 0/1/2，v=波动档 0/1。

    未收盘占位（is_closed=False）返回 None——旧入口必须保持不可用（regime -1）。
    """
    if not ms.is_closed:
        return None
    adx = ms.adx
    if adx is None or adx < _ADX_RANGING:
        t = 0
    elif adx < _ADX_STRONG:
        t = 1
    else:
        t = 2
    pct = ms.vol_percentile
    v = 0 if (pct is None or pct < _ATR_PCT_SPLIT) else 1
    return t, v


def alignment_from_state(ms: MarketState) -> int:
    """旧 MA 排列口径（20/60/120）：+2/+1/-2/-1/0。"""
    f, m, s = ms.ma20, ms.ma60, ms.ma120
    price = ms.close
    if any(v is None for v in (f, m, s, price)):
        return 0
    if price > f > m > s:  # type: ignore[operator]
        return 2
    if price > f > m:  # type: ignore[operator]
        return 1
    if price < f < m < s:  # type: ignore[operator]
        return -2
    if price < f < m:  # type: ignore[operator]
        return -1
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
#  → 旧 trend_classifier 五态（摆动 + MA20/60 + 价格位置三维打分）
# ═══════════════════════════════════════════════════════════════════════════════

def trend_regime_from_state(ms: MarketState) -> int | None:
    """旧三维打分 → 五态 ID（2 强升 / 1 弱升 / 0 震荡 / -1 弱降 / -2 强降）。

    未收盘占位（is_closed=False）返回 None——旧入口必须保持不可用（trend -99）。
    """
    if not ms.is_closed:
        return None
    score = ms.swing_score + ms.ma_direction_score + ms.price_position_score
    if score >= 4:
        return 2
    if score >= 2:
        return 1
    if score >= -1:
        return 0
    if score >= -3:
        return -1
    return -2


def market_state_to_trend_array(states: list[MarketState]) -> list[int]:
    """MarketState 序列 → 旧 classify() 输出（含稳定过滤，行为与旧实现逐位一致）。

    旧实现细节（必须复现）：
      - n < 61 → 全 -99（由调用方 guard）；
      - raw 只在 [60, n) 计算，其余 -99；
      - 稳定过滤从 64 起：窗口过去 5 根，有效分类 ≥4 且多数 ≥4 才定值。
    """
    n = len(states)
    raw = [-99] * n
    for i in range(60, n):
        value = trend_regime_from_state(states[i])
        if value is not None:
            raw[i] = value
        # 未收盘占位 → 保持 -99（不可用）
    stable = [-99] * n
    for i in range(64, n):
        if not states[i].is_closed:
            continue  # 未收盘占位 → 保持 -99（不可用）
        window = raw[i - 4:i + 1]
        valid = [r for r in window if r != -99]
        if len(valid) < 4:
            continue
        dominant_id, dominant_count = Counter(valid).most_common(1)[0]
        if dominant_count >= 4:
            stable[i] = dominant_id
    return stable


# ═══════════════════════════════════════════════════════════════════════════════
#  → 日报 stage / tactical_phase / conclusion
# ═══════════════════════════════════════════════════════════════════════════════
# 逻辑与旧 daily_report_analysis._market_regime / _relative_state / _slope_state
# 同源（逐字复现，包括其中保守判定），由 MarketState.features 驱动。

MA_PERIODS = (20, 60, 120)
MA_POSITION_ATR_THRESHOLD = 0.10
MA_SLOPE_ATR_THRESHOLD = 0.05


def _relative_state(value: float | None, reference: float | None, atr: float | None) -> str:
    if reference is None or atr is None or atr <= 0:
        return "unknown"
    distance = (value - reference) / atr
    if distance > MA_POSITION_ATR_THRESHOLD:
        return "above"
    if distance < -MA_POSITION_ATR_THRESHOLD:
        return "below"
    return "nearby"


def _slope_state(current: float | None, prev2: float | None, atr: float | None) -> str:
    if current is None or prev2 is None or atr is None or atr <= 0:
        return "unknown"
    slope = (current - prev2) / atr
    if slope > MA_SLOPE_ATR_THRESHOLD:
        return "up"
    if slope < -MA_SLOPE_ATR_THRESHOLD:
        return "down"
    return "flat"


def _daily_positions_slopes(ms: MarketState) -> tuple[dict[str, str], dict[str, str]]:
    positions: dict[str, str] = {}
    slopes: dict[str, str] = {}
    for period in MA_PERIODS:
        ma_val = getattr(ms, f"ma{period}")
        ma_prev2 = getattr(ms, f"ma{period}_prev2")
        positions[f"ma{period}"] = _relative_state(ms.close, ma_val, ms.atr)
        slopes[f"ma{period}"] = _slope_state(ma_val, ma_prev2, ms.atr)
    return positions, slopes


def _market_regime(
    close: float | None,
    latest_ma: dict[int, float | None],
    positions: dict[str, str],
    slopes: dict[str, str],
) -> tuple[str, str, str]:
    """旧 daily_report_analysis._market_regime 逐字复现（含保守判定）。"""
    ma20, ma60, ma120 = latest_ma[20], latest_ma[60], latest_ma[120]
    if any(value is None for value in (ma20, ma60, ma120)):
        return "数据不足", "unclear", "均线历史不足，暂时无法判断市场趋势。"

    bullish = (
        (
            positions["ma120"] == "above"
            and float(ma60) > float(ma120)
            and slopes["ma60"] == "up"
            and slopes["ma120"] in {"up", "flat"}
        )
        or (
            all(positions[f"ma{period}"] == "above" for period in MA_PERIODS)
            and all(slopes[f"ma{period}"] == "up" for period in MA_PERIODS)
        )
    )
    bearish = (
        (
            positions["ma120"] == "below"
            and float(ma60) < float(ma120)
            and slopes["ma60"] == "down"
            and slopes["ma120"] in {"down", "flat"}
        )
        or (
            all(positions[f"ma{period}"] == "below" for period in MA_PERIODS)
            and all(slopes[f"ma{period}"] == "down" for period in MA_PERIODS)
        )
    )
    strong_bullish = (
        close is not None
        and close > float(ma20) > float(ma60) > float(ma120)
        and all(slopes[f"ma{period}"] == "up" for period in MA_PERIODS)
    )
    strong_bearish = (
        close is not None
        and close < float(ma20) < float(ma60) < float(ma120)
        and all(slopes[f"ma{period}"] == "down" for period in MA_PERIODS)
    )

    if bullish:
        pullback = positions["ma20"] != "above" or slopes["ma20"] == "down"
        if strong_bullish:
            return "上涨趋势", "continuation", "市场处于强劲上涨趋势，三条均线形成多头排列且方向向上。"
        if pullback:
            return "上涨趋势", "pullback", "中长期上涨结构尚未改变，但市场正在经历短期回调。"
        return "上涨趋势", "continuation", "市场处于上涨趋势，当前结构仍然偏多。"

    if bearish:
        rebound = positions["ma20"] != "below" or slopes["ma20"] == "up"
        if strong_bearish:
            return "下跌趋势", "continuation", "市场处于强劲下跌趋势，三条均线形成空头排列且方向向下。"
        if rebound:
            return "下跌趋势", "rebound", "中长期下跌结构尚未改变，当前属于下跌趋势中的短期反弹。"
        return "下跌趋势", "continuation", "市场处于下跌趋势，当前结构仍然偏空。"

    return "趋势不明确", "unclear", "均线结构交错，市场方向尚不明确。"


def daily_stage_from_state(ms: MarketState) -> tuple[str, str, str]:
    """MarketState → 日报 (stage, tactical_phase, conclusion)。

    旧 analyze_instrument 在 len(candles) >= 122 时才判定 stage；
    等价条件 ms.bar_index + 1 >= 122（bar_index 为 0-based）。
    未收盘占位（is_closed=False）→ 数据不足（不可用）。
    """
    if not ms.is_closed:
        return "数据不足", "unclear", "均线历史不足，暂时无法判断市场趋势。"
    if ms.bar_index + 1 < 122:
        return "数据不足", "unclear", "均线历史不足，暂时无法判断市场趋势。"
    latest_ma = {period: getattr(ms, f"ma{period}") for period in MA_PERIODS}
    positions, slopes = _daily_positions_slopes(ms)
    return _market_regime(ms.close, latest_ma, positions, slopes)
