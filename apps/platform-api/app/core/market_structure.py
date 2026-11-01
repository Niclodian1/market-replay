"""
市场结构分析：Swing Point 检测、HH/HL/LH/LL 标注、结构突破、支撑阻力位。
"""
from typing import Optional
import math


def detect_swing_points(
    candles: list[dict], lookback: int = 5
) -> list[dict]:
    """
    Williams Fractal: 某根K线的 high 是前后各 lookback 根中最高 → swing_high，
    low 是最低 → swing_low。同一根K线可同时是 swing_high 和 swing_low。
    """
    n = len(candles)
    swings: list[dict] = []
    for i in range(lookback, n - lookback):
        hi = candles[i]["high"]
        lo = candles[i]["low"]
        window = candles[i - lookback : i + lookback + 1]
        if hi == max(c["high"] for c in window):
            swings.append({"time": candles[i]["time"], "price": hi, "type": "swing_high", "index": i})
        if lo == min(c["low"] for c in window):
            swings.append({"time": candles[i]["time"], "price": lo, "type": "swing_low", "index": i})
    return swings


def label_structure(swing_points: list[dict]) -> list[dict]:
    """
    对 swing points 标注 HH/HL/LH/LL。
    与前一个同类型比较: swing_high > prev swing_high → HH, 否则 LH;
    swing_low > prev swing_low → HL, 否则 LL。
    """
    labeled: list[dict] = []
    prev_high: Optional[dict] = None
    prev_low: Optional[dict] = None

    for sp in swing_points:
        entry = {**sp}
        if sp["type"] == "swing_high":
            if prev_high is not None:
                entry["label"] = "HH" if sp["price"] > prev_high["price"] else "LH"
            else:
                entry["label"] = "HH"
            prev_high = sp
        else:
            if prev_low is not None:
                entry["label"] = "HL" if sp["price"] > prev_low["price"] else "LL"
            else:
                entry["label"] = "HL"
            prev_low = sp
        labeled.append(entry)
    return labeled


def detect_structure_breaks(
    candles: list[dict], labeled_swings: list[dict]
) -> list[dict]:
    """
    结构突破: 价格收盘突破最近的 LH（看涨BOS）或 HL（看跌BOS）。
    """
    breaks: list[dict] = []
    last_lh: Optional[dict] = None
    last_hl: Optional[dict] = None

    for c in candles:
        for s in labeled_swings:
            if s["time"] > c["time"]:
                break
            if s.get("label") == "LH":
                last_lh = s
            elif s.get("label") == "HL":
                last_hl = s

        if last_lh and c["close"] > last_lh["price"]:
            if not breaks or breaks[-1].get("direction") != "bullish" or breaks[-1]["broken_level"] != last_lh["price"]:
                breaks.append({
                    "time": c["time"],
                    "price": c["close"],
                    "direction": "bullish",
                    "broken_level": last_lh["price"],
                })
                last_lh = None

        if last_hl and c["close"] < last_hl["price"]:
            if not breaks or breaks[-1].get("direction") != "bearish" or breaks[-1]["broken_level"] != last_hl["price"]:
                breaks.append({
                    "time": c["time"],
                    "price": c["close"],
                    "direction": "bearish",
                    "broken_level": last_hl["price"],
                })
                last_hl = None

    return breaks


def detect_sr_levels(
    candles: list[dict],
    swing_points: list[dict],
    tolerance_pct: float = 0.5,
    max_levels: int = 10,
) -> list[dict]:
    """
    支撑阻力位检测：
    1. 按价格聚类 swing points（容差 tolerance_pct%）
    2. 评分 = 触碰次数 × 近期权重 × 整数关口加权
    """
    if not swing_points:
        return []

    prices_with_meta = [
        {"price": sp["price"], "type": sp["type"], "index": sp.get("index", 0)}
        for sp in swing_points
    ]
    prices_with_meta.sort(key=lambda x: x["price"])

    clusters: list[dict] = []
    used: set[int] = set()
    total_candles = len(candles)

    for i, pm in enumerate(prices_with_meta):
        if i in used:
            continue
        cluster_prices = [pm["price"]]
        cluster_indices = [pm["index"]]
        cluster_types = [pm["type"]]
        used.add(i)
        for j in range(i + 1, len(prices_with_meta)):
            if j in used:
                continue
            if abs(prices_with_meta[j]["price"] - pm["price"]) / pm["price"] < tolerance_pct / 100:
                cluster_prices.append(prices_with_meta[j]["price"])
                cluster_indices.append(prices_with_meta[j]["index"])
                cluster_types.append(prices_with_meta[j]["type"])
                used.add(j)

        avg_price = sum(cluster_prices) / len(cluster_prices)
        touch_count = len(cluster_prices)

        recency_score = sum(
            1.0 + (idx / total_candles) for idx in cluster_indices
        ) / touch_count if total_candles > 0 else 1.0

        round_bonus = 1.0
        if avg_price > 0:
            magnitude = 10 ** max(0, int(math.log10(avg_price)) - 1)
            if magnitude > 0 and avg_price % magnitude < magnitude * 0.02:
                round_bonus = 1.3

        strength = touch_count * recency_score * round_bonus

        high_count = sum(1 for t in cluster_types if t == "swing_high")
        low_count = sum(1 for t in cluster_types if t == "swing_low")
        level_type = "resistance" if high_count > low_count else "support" if low_count > high_count else "support_resistance"

        clusters.append({
            "price": round(avg_price, 4),
            "strength": round(strength, 2),
            "touch_count": touch_count,
            "type": level_type,
        })

    clusters.sort(key=lambda x: x["strength"], reverse=True)
    return clusters[:max_levels]


def determine_trend(labeled_swings: list[dict]) -> str:
    """根据最近的结构标注判定趋势方向。"""
    recent = labeled_swings[-6:] if len(labeled_swings) >= 6 else labeled_swings
    labels = [s.get("label", "") for s in recent]
    hh_hl = sum(1 for l in labels if l in ("HH", "HL"))
    ll_lh = sum(1 for l in labels if l in ("LL", "LH"))
    if hh_hl > ll_lh + 1:
        return "uptrend"
    if ll_lh > hh_hl + 1:
        return "downtrend"
    return "ranging"


def analyze_structure(
    candles: list[dict], lookback: int = 5, tolerance_pct: float = 0.5, max_sr: int = 10
) -> dict:
    """完整市场结构分析，返回所有结果。"""
    swings = detect_swing_points(candles, lookback)
    labeled = label_structure(swings)
    breaks = detect_structure_breaks(candles, labeled)
    sr_levels = detect_sr_levels(candles, swings, tolerance_pct, max_sr)
    trend = determine_trend(labeled)

    result_swings = [
        {"time": s["time"], "price": s["price"], "type": s["type"], "label": s.get("label", s["type"])}
        for s in labeled
    ]
    result_breaks = [
        {"time": b["time"], "price": b["price"], "direction": b["direction"], "broken_level": b["broken_level"]}
        for b in breaks
    ]

    return {
        "swing_points": result_swings,
        "structure_breaks": result_breaks,
        "trend": trend,
        "sr_levels": sr_levels,
    }
