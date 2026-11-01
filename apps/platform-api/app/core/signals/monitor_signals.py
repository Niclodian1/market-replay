"""Pure, closed-bar detectors used by watchlist monitor plans."""
from __future__ import annotations

from typing import Any, Iterable

from app.core.indicators import _atr, _macd, _sma
from app.core.signals.evidence import (
    build_signal_payload,
    ma_breakout_evidence,
    volume_evidence,
)


def _event(
    *,
    strategy: str,
    event_type: str,
    direction: str,
    severity: str,
    summary: str,
    candle: dict[str, Any],
    timeframe: str,
    evidence: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    payload = build_signal_payload(
        signal_type=event_type,
        direction=direction,
        state=severity,
        candle=candle,
        timeframe=timeframe,
        evidence=evidence,
        parameters=parameters,
    )
    return {
        "strategy": strategy,
        "event_type": event_type,
        "direction": direction,
        "severity": severity,
        "signal_time": int(candle["time"]),
        "reference_price": float(candle["close"]),
        "summary": summary,
        "payload": payload,
    }


def detect_ma_events(
    candles: list[dict[str, Any]],
    timeframe: str,
    *,
    periods: Iterable[int] = (30, 60, 99, 120),
    atr_period: int = 14,
    min_body_atr: float = 0.5,
    min_close_location: float = 0.55,
) -> list[dict[str, Any]]:
    """ATR-normalised MA breakout signals（只记录突破，不回踩）。"""
    period_values = sorted(set(int(value) for value in periods if int(value) >= 2))
    if not period_values or len(candles) < max([*period_values, atr_period]) + 2:
        return []
    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    atr_values = _atr(highs, lows, closes, atr_period)
    index = len(candles) - 1
    atr_now = atr_values[index]
    if atr_now is None or atr_now <= 0:
        return []

    output: list[dict[str, Any]] = []
    for period in period_values:
        ma = _sma(closes, period)
        if ma[index] is None or ma[index - 1] is None:
            continue
        for direction, sign in (("long", 1), ("short", -1)):
            crossed = (
                closes[index - 1] <= float(ma[index - 1]) and closes[index] > float(ma[index])
                if direction == "long"
                else closes[index - 1] >= float(ma[index - 1]) and closes[index] < float(ma[index])
            )
            evidence = ma_breakout_evidence(candles, index, period, direction, atr_period)
            body_atr = evidence["price"]["body_atr"] or 0.0
            close_location = evidence["price"]["close_location"] or 0.0
            parameters = {
                "ma_type": "SMA",
                "period": period,
                "atr_period": atr_period,
                "min_body_atr": min_body_atr,
                "min_close_location": min_close_location,
            }
            if crossed and body_atr >= min_body_atr and close_location >= min_close_location:
                ma_info = evidence["ma"]
                slope = ma_info.get("slope_atr_per_bar")
                slope_text = ""
                if slope is not None:
                    consistent = (
                        (direction == "long" and slope >= 0)
                        or (direction == "short" and slope <= 0)
                    )
                    flag = "" if consistent else "⚠斜率指标不一致"
                    slope_text = f"，MA{period} 斜率 {slope:+.2f} ATR/根{flag}"
                output.append(_event(
                    strategy="ma",
                    event_type="ma_breakout",
                    direction=direction,
                    severity="signal",
                    summary=(
                        f"收盘{'上穿' if direction == 'long' else '下穿'} MA{period}，"
                        f"实体 {body_atr:.2f} ATR，成交量状态 {evidence['volume']['strength']}"
                        f"{slope_text}"
                    ),
                    candle=candles[index], timeframe=timeframe,
                    evidence=evidence, parameters=parameters,
                ))
    return output


def _hist_sign(value: float | None) -> int:
    if value is None or value == 0:
        return 0
    return 1 if value > 0 else -1


def _segment_ranges(hist: list[float | None]) -> dict[int, list[tuple[int, int]]]:
    ranges: dict[int, list[tuple[int, int]]] = {-1: [], 1: []}
    start = 0
    sign = 0
    for i, value in enumerate(hist + [0.0]):
        now = _hist_sign(value)
        if now == sign:
            continue
        if sign:
            ranges[sign].append((start, i - 1))
        sign = now
        start = i
    return ranges


def _confirmation_events(
    candles: list[dict[str, Any]],
    *, fast: int, slow: int, signal: int, atr_period: int,
) -> list[dict[str, Any] | None]:
    """First causal bar where price-extreme plus weaker-area divergence is true."""
    n = len(candles)
    results: list[dict[str, Any] | None] = [None] * n
    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    _, _, optional_hist = _macd(closes, fast, slow, signal)
    hist = [None if value is None else float(value) for value in optional_hist]
    atr = _atr(highs, lows, closes, atr_period)
    ranges = _segment_ranges(hist)

    for sign in (-1, 1):
        same_sign = ranges[sign]
        for segment_index in range(1, len(same_sign)):
            current_start, current_end = same_sign[segment_index]
            previous_start, previous_end = same_sign[segment_index - 1]
            previous_values = [abs(float(hist[j] or 0)) for j in range(previous_start, previous_end + 1)]
            previous_norm = [
                abs(float(hist[j] or 0)) / float(atr[j]) if atr[j] else abs(float(hist[j] or 0))
                for j in range(previous_start, previous_end + 1)
            ]
            previous_extreme = (
                min(lows[previous_start:previous_end + 1])
                if sign < 0 else max(highs[previous_start:previous_end + 1])
            )
            confirmed = False
            for i in range(current_start + 3, current_end + 1):
                current_values = [abs(float(hist[j] or 0)) for j in range(current_start, i + 1)]
                peak_offset = max(range(len(current_values)), key=current_values.__getitem__)
                peak_index = current_start + peak_offset
                if i <= peak_index or peak_index - current_start < 2:
                    continue
                current_norm = [
                    abs(float(hist[j] or 0)) / float(atr[j]) if atr[j] else abs(float(hist[j] or 0))
                    for j in range(current_start, i + 1)
                ]
                aligned = min(len(current_norm), len(previous_norm))
                previous_area = sum(previous_norm[:aligned])
                aligned_ratio = sum(current_norm[:aligned]) / previous_area if previous_area else None
                average_ratio = (
                    (sum(current_norm) / len(current_norm)) / (sum(previous_norm) / len(previous_norm))
                    if previous_norm and sum(previous_norm) else None
                )
                peak_ratio = max(current_values) / max(previous_values) if previous_values and max(previous_values) else None
                weak_votes = sum(
                    ratio is not None and ratio < 1.0
                    for ratio in (aligned_ratio, average_ratio, peak_ratio)
                )
                current_extreme = min(lows[current_start:i + 1]) if sign < 0 else max(highs[current_start:i + 1])
                new_extreme = current_extreme < previous_extreme if sign < 0 else current_extreme > previous_extreme
                if aligned_ratio is None or aligned_ratio >= 1.0 or weak_votes < 2 or not new_extreme:
                    continue
                results[i] = {
                    "direction": "long" if sign < 0 else "short",
                    "state": "confirmed_divergence",
                    "histogram": {"current": hist[i], "peak": hist[peak_index]},
                    "comparisons": {
                        "aligned_area_ratio": aligned_ratio,
                        "average_intensity_ratio": average_ratio,
                        "peak_ratio": peak_ratio,
                    },
                    "conditions": {
                        "price_made_new_extreme": True,
                        "regular_divergence": True,
                    },
                    "segments": {
                        "current": [current_start, i],
                        "previous": [previous_start, previous_end],
                    },
                }
                confirmed = True
                break
            if confirmed:
                continue
    return results


def detect_macd_events(
    candles: list[dict[str, Any]],
    timeframe: str,
    *,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> list[dict[str, Any]]:
    """柱极值背离·动能极值相对减弱（替换原面积式确认检测器）。

    当前动能段极值 vs 穿过≥3根反向柱的前一同向段极值；强度>0 即标记，
    ≥0.7强 / ≥0.4中 / 其余弱。只返回最新已完成 bar 上的事件。
    """
    from app.core.divergence_detectors import macd_extreme_divergence_events

    if len(candles) < slow + signal + 5:
        return []
    index = len(candles) - 1
    params = {"fast": fast, "slow": slow, "signal": signal}
    events = macd_extreme_divergence_events(candles)
    output: list[dict[str, Any]] = []
    latest_ts = int(candles[index]["time"])
    for ev in events:
        if int(ev["t"]) != latest_ts:
            continue  # 只报告最新已完成 bar 上的背离
        side = "空头" if ev["side"] == "buy" else "多头"
        output.append(_event(
            strategy="macd", event_type="macd_extreme_divergence",
            direction=ev["side"], severity="signal",
            summary=(
                f"{side}柱极值背离（{ev['strength_tier']}·强度{ev['strength']}）："
                f"动能相对前一同向段极值减弱"
            ),
            candle=candles[index], timeframe=timeframe,
            evidence=ev, parameters=params,
        ))
    return output


def _swings(candles: list[dict[str, Any]], pivot: int = 3) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    # Excluding the last pivot bars makes every swing causally confirmed.
    for i in range(pivot, len(candles) - pivot):
        window = candles[i - pivot:i + pivot + 1]
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        if high == max(float(c["high"]) for c in window):
            highs.append((i, high))
        if low == min(float(c["low"]) for c in window):
            lows.append((i, low))
    return highs, lows


def _fit(points: list[tuple[int, float]]) -> tuple[float, float]:
    count = len(points)
    mean_x = sum(x for x, _ in points) / count
    mean_y = sum(y for _, y in points) / count
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator if denominator else 0.0
    return slope, mean_y - slope * mean_x


def _line_evidence(points: list[tuple[int, float]], slope: float, intercept: float, atr: float) -> dict[str, Any]:
    residuals = [abs(price - (slope * index + intercept)) / atr for index, price in points]
    return {
        "slope_per_bar": slope,
        "intercept": intercept,
        "touches": len(points),
        "touch_times": [index for index, _ in points],
        "mean_residual_atr": sum(residuals) / len(residuals),
        "max_residual_atr": max(residuals),
    }


def detect_structure_events(
    candles: list[dict[str, Any]],
    timeframe: str,
    *,
    pivot: int = 3,
    min_touches: int = 3,
    fit_points: int = 5,
    max_residual_atr: float = 0.75,
    breakout_buffer_atr: float = 0.1,
    convergence_ratio: float = 0.65,
) -> list[dict[str, Any]]:
    """Explainable trendline and converging-structure close breakouts."""
    if len(candles) < 50:
        return []
    closes = [float(c["close"]) for c in candles]
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    atr = _atr(highs, lows, closes, 14)[-1]
    if atr is None or atr <= 0:
        return []
    swing_highs, swing_lows = _swings(candles, pivot)
    if len(swing_highs) < min_touches or len(swing_lows) < min_touches:
        return []
    upper_points = swing_highs[-fit_points:]
    lower_points = swing_lows[-fit_points:]
    if len(upper_points) < min_touches or len(lower_points) < min_touches:
        return []
    upper_slope, upper_intercept = _fit(upper_points)
    lower_slope, lower_intercept = _fit(lower_points)
    upper_evidence = _line_evidence(upper_points, upper_slope, upper_intercept, float(atr))
    lower_evidence = _line_evidence(lower_points, lower_slope, lower_intercept, float(atr))
    if (
        upper_evidence["max_residual_atr"] > max_residual_atr
        or lower_evidence["max_residual_atr"] > max_residual_atr
    ):
        return []

    index = len(candles) - 1
    previous = index - 1
    upper_now = upper_slope * index + upper_intercept
    upper_previous = upper_slope * previous + upper_intercept
    lower_now = lower_slope * index + lower_intercept
    lower_previous = lower_slope * previous + lower_intercept
    buffer = float(atr) * breakout_buffer_atr
    direction = None
    if closes[previous] <= upper_previous + buffer and closes[index] > upper_now + buffer:
        direction = "long"
    elif closes[previous] >= lower_previous - buffer and closes[index] < lower_now - buffer:
        direction = "short"
    if direction is None:
        return []

    common_start = max(upper_points[0][0], lower_points[0][0])
    width_start = (
        upper_slope * common_start + upper_intercept
        - (lower_slope * common_start + lower_intercept)
    )
    width_now = upper_now - lower_now
    contraction = width_now / width_start if width_start > 0 else None
    converging = bool(
        upper_slope < lower_slope
        and contraction is not None
        and 0 < contraction <= convergence_ratio
    )
    event_type = "convergence_breakout" if converging else "trendline_breakout"
    evidence = {
        "upper_line": {**upper_evidence, "value": upper_now},
        "lower_line": {**lower_evidence, "value": lower_now},
        "structure": {
            "converging": converging,
            "width_start": width_start,
            "width_now": width_now,
            "contraction_ratio": contraction,
        },
        "breakout": {"buffer_atr": breakout_buffer_atr, "close_confirmed": True},
        "atr": {"period": 14, "value": atr},
        "volume": volume_evidence(candles),
    }
    return [_event(
        strategy="structure", event_type=event_type, direction=direction,
        severity="confirmed",
        summary=(
            f"收盘突破{'收敛结构' if converging else '趋势线'}，"
            f"成交量状态 {evidence['volume']['strength']}"
        ),
        candle=candles[index], timeframe=timeframe, evidence=evidence,
        parameters={
            "pivot": pivot, "min_touches": min_touches,
            "max_residual_atr": max_residual_atr,
            "breakout_buffer_atr": breakout_buffer_atr,
            "convergence_ratio": convergence_ratio,
        },
    )]


DETECTORS = {
    "ma": detect_ma_events,
    "macd": detect_macd_events,
    "structure": detect_structure_events,
}


def run_monitor_detectors(
    candles: list[dict[str, Any]],
    timeframe: str,
    strategies: Iterable[str],
    parameters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    parameters = parameters or {}
    events: list[dict[str, Any]] = []
    for strategy in strategies:
        detector = DETECTORS.get(strategy)
        if detector is None:
            continue
        events.extend(detector(candles, timeframe, **parameters.get(strategy, {})))
    return events
