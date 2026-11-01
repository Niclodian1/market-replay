"""结构化信号证据。

这个模块只做纯计算，不发送通知、不访问数据库。告警和回测探测器消费同一份
结果，避免「界面描述」与「实际触发数学」逐渐分叉。
"""

from __future__ import annotations

from statistics import median
from typing import Any, Iterable

from app.core.indicators import _atr, _sma


SCHEMA_VERSION = 1


def _round(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(float(value), digits)


def _mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return sum(values) / len(values) if values else None


def build_signal_payload(
    *,
    signal_type: str,
    direction: str,
    state: str,
    candle: dict[str, Any],
    timeframe: str,
    evidence: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """所有探测器共享的可序列化数据模板。"""
    timestamp = int(candle["time"])
    return {
        "schema_version": SCHEMA_VERSION,
        "signal_type": signal_type,
        "direction": direction,
        "state": state,
        "timeframe": timeframe,
        "occurred_at": timestamp,
        "confirmed_at": timestamp,
        "reference_price": float(candle["close"]),
        # 探测器只应传入已完成的历史 K 线；若行情源显式标记则尊重它。
        "bar_closed": bool(candle.get("is_closed", True)),
        "evidence": evidence,
        "parameters": parameters,
    }


def volume_evidence(
    candles: list[dict[str, Any]],
    index: int = -1,
    windows: tuple[int, ...] = (5, 10),
) -> dict[str, Any]:
    """当前成交量相对先前完整 K 线的均值/中位数比值。

    当前 K 线严格排除在基准窗口之外。缺少完整窗口时返回 ``None``，而不是
    用较短窗口伪装成用户配置的 5/10 根基准。
    """
    if not candles:
        return {"current": None, "strength": "unknown", "windows": {}}
    if index < 0:
        index += len(candles)
    if index < 0 or index >= len(candles):
        raise IndexError("volume evidence index out of range")

    current = float(candles[index].get("volume") or 0.0)
    output: dict[str, Any] = {
        "current": _round(current),
        "strength": "unknown",
        "windows": {},
    }
    preferred_ratio: float | None = None

    for window in sorted(set(windows)):
        prior = [float(c.get("volume") or 0.0) for c in candles[max(0, index - window):index]]
        if len(prior) != window:
            output["windows"][str(window)] = {
                "average": None,
                "median": None,
                "ratio_to_average": None,
                "ratio_to_median": None,
                "sample_size": len(prior),
            }
            continue
        average = _mean(prior)
        median_value = float(median(prior))
        ratio_average = current / average if average and average > 0 else None
        ratio_median = current / median_value if median_value > 0 else None
        output["windows"][str(window)] = {
            "average": _round(average),
            "median": _round(median_value),
            "ratio_to_average": _round(ratio_average),
            "ratio_to_median": _round(ratio_median),
            "sample_size": window,
        }
        preferred_ratio = ratio_average

    if preferred_ratio is not None:
        if preferred_ratio < 0.8:
            output["strength"] = "low"
        elif preferred_ratio < 1.2:
            output["strength"] = "normal"
        elif preferred_ratio < 1.8:
            output["strength"] = "elevated"
        else:
            output["strength"] = "high"
    return output


def format_volume_evidence(evidence: dict[str, Any]) -> str:
    labels = {"low": "缩量", "normal": "正常", "elevated": "温和放量", "high": "明显放量"}
    pieces = []
    for window in ("5", "10"):
        ratio = evidence.get("windows", {}).get(window, {}).get("ratio_to_average")
        if ratio is not None:
            pieces.append(f"过去{window}根的 **{ratio:.2f} 倍**")
    if not pieces:
        return "成交量历史不足，暂不能计算量能比值"
    strength = labels.get(evidence.get("strength"), "未知")
    return f"成交量为{'、'.join(pieces)}，量能状态：**{strength}**"


def ma_breakout_evidence(
    candles: list[dict[str, Any]],
    index: int,
    period: int,
    direction: str,
    atr_period: int = 14,
) -> dict[str, Any]:
    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    ma = _sma(closes, period)[index]
    atr = _atr(highs, lows, closes, atr_period)[index]
    candle = candles[index]
    open_price = float(candle["open"])
    close = closes[index]
    high = highs[index]
    low = lows[index]
    sign = 1.0 if direction == "long" else -1.0
    distance = sign * (close - float(ma)) if ma is not None else None
    candle_range = high - low

    ma_series = _sma(closes, period)
    slope = _ma_slope(ma_series, index, window=period)

    return {
        "ma": {
            "type": "SMA",
            "period": period,
            "value": _round(ma),
            "distance_pct": _round(sign * (close / float(ma) - 1.0) * 100) if ma else None,
            "distance_atr": _round(distance / float(atr)) if distance is not None and atr else None,
            "slope_per_bar": _round(slope, 4),
            "slope_atr_per_bar": _round(slope / float(atr), 4) if slope is not None and atr else None,
        },
        "price": {
            "open": _round(open_price),
            "high": _round(high),
            "low": _round(low),
            "close": _round(close),
            "body_atr": _round(abs(close - open_price) / float(atr)) if atr else None,
            "close_location": _round(
                ((close - low) / candle_range) if direction == "long" else ((high - close) / candle_range)
            ) if candle_range > 0 else None,
        },
        "atr": {"period": atr_period, "value": _round(atr)},
        "volume": volume_evidence(candles, index),
    }


def _ma_slope(ma_series: list[float | None], index: int, window: int) -> float | None:
    """最近 window 根 MA 的线性斜率（每根K线的价格变化量）。

    使用首尾差分近似斜率，对 MA 平滑后的序列足够稳健。
    """
    points: list[tuple[int, float]] = []
    for offset in range(index - window + 1, index + 1):
        if offset < 0:
            continue
        value = ma_series[offset]
        if value is None:
            continue
        points.append((offset, float(value)))
    if len(points) < 2:
        return None
    count = len(points)
    mean_x = sum(x for x, _ in points) / count
    mean_y = sum(y for _, y in points) / count
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    if denominator == 0:
        return 0.0
    return sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator


