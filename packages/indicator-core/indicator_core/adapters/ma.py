"""均线突破 adapter（``ma.breakout``）。

语义与 ``app.core.signals.monitor_signals.detect_ma_events`` 对齐，区别只有一点：
那边只看最后一根（轮询用），这里扫描全史（图表/统计/实验用）。

事件锚点 = 突破所在 K 线的时间（该根收盘即可确认），因此 ``confirmed_at == bar_time``。
"""
from __future__ import annotations

from typing import Any, Sequence

from ..series import atr_series, ma_series
from ..spec import Event, IndicatorSpec, params_hash

_SUPPORTED_TFS = ("15min", "1h", "4h", "1day", "1week")

_PARAMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "periods": {
            "type": "array",
            "items": {"type": "integer", "minimum": 2},
            "default": [30, 60],
            "title": "均线周期",
        },
        "kind": {"type": "string", "enum": ["sma", "ema"], "default": "sma"},
        "atr_period": {"type": "integer", "default": 14, "minimum": 2},
        "min_body_atr": {
            "type": "number",
            "default": 0.5,
            "minimum": 0,
            "title": "最小实体（ATR 倍数）",
        },
        "min_close_location": {
            "type": "number",
            "default": 0.55,
            "minimum": 0,
            "maximum": 1,
            "title": "收盘位置（0=最低 1=最高）",
        },
    },
    "additionalProperties": False,
}

_SPEC = IndicatorSpec(
    id="ma.breakout",
    label="均线突破",
    group="ma",
    timeframes=_SUPPORTED_TFS,
    warmup_bars=60,
    direction_support=("long", "short"),
    description="收盘穿越均线，且实体与收盘位置过滤；只记录突破，不回踩",
    logic="SMA/EMA 穿越 + body_atr >= 阈值 + close_location >= 阈值（与 monitor 口径一致）",
    params_schema=_PARAMS_SCHEMA,
)


class MaBreakoutDetector:
    spec = _SPEC

    def detect(
        self,
        bars: Sequence[dict[str, Any]],
        symbol: str,
        timeframe: str,
        params: dict[str, Any],
    ) -> list[Event]:
        periods = sorted({int(p) for p in params.get("periods", [30, 60])})
        kind = str(params.get("kind", "sma"))
        atr_period = int(params.get("atr_period", 14))
        min_body_atr = float(params.get("min_body_atr", 0.5))
        min_close_location = float(params.get("min_close_location", 0.55))

        if not periods or any(period < 2 for period in periods):
            raise ValueError("periods 必须是 >= 2 的整数列表")
        if kind not in ("sma", "ema"):
            raise ValueError("kind 只能是 sma / ema")
        warmup = max(max(periods), atr_period) + 2
        if len(bars) < warmup:
            return []

        closes = [float(bar["close"]) for bar in bars]
        opens = [float(bar["open"]) for bar in bars]
        highs = [float(bar["high"]) for bar in bars]
        lows = [float(bar["low"]) for bar in bars]
        volumes = [float(bar.get("volume") or 0.0) for bar in bars]

        atr = atr_series(bars, atr_period)[0].values
        lines = ma_series(bars, periods, kind)
        normalized = {
            "periods": periods,
            "kind": kind,
            "atr_period": atr_period,
            "min_body_atr": min_body_atr,
            "min_close_location": min_close_location,
        }
        p_hash = params_hash(normalized)
        vol_window = [None] * len(volumes)
        running = 0.0
        for i, value in enumerate(volumes):
            running += value
            if i >= 20:
                running -= volumes[i - 20]
            if i >= 19:
                vol_window[i] = running / 20

        events: list[Event] = []
        for line, period in zip(lines, periods):
            ma = line.values
            for i in range(1, len(bars)):
                if ma[i] is None or ma[i - 1] is None or atr[i] is None or atr[i] <= 0:
                    continue
                prev_close, close = closes[i - 1], closes[i]
                cross_long = prev_close <= ma[i - 1] and close > ma[i]
                cross_short = prev_close >= ma[i - 1] and close < ma[i]
                if not (cross_long or cross_short):
                    continue
                direction = "long" if cross_long else "short"
                candle_range = highs[i] - lows[i]
                if candle_range <= 0:
                    continue
                body_atr = abs(close - opens[i]) / atr[i]
                close_location = (
                    (close - lows[i]) / candle_range
                    if direction == "long"
                    else (highs[i] - close) / candle_range
                )
                if body_atr < min_body_atr or close_location < min_close_location:
                    continue
                sign = 1.0 if direction == "long" else -1.0
                slope = None
                if i >= period and ma[i - period] is not None:
                    slope = (ma[i] - ma[i - period]) / (period - 1)
                volume_ratio = (
                    volumes[i] / vol_window[i]
                    if vol_window[i] not in (None, 0.0)
                    else None
                )
                events.append(
                    Event(
                        indicator_id=self.spec.id,
                        symbol=symbol.upper(),
                        timeframe=timeframe,
                        bar_time=int(bars[i]["time"]),
                        confirmed_at=int(bars[i]["time"]),
                        direction=direction,  # type: ignore[arg-type]
                        price=close,
                        params_hash=p_hash,
                        strength=round(min(1.0, body_atr / 2.0), 4),
                        title=f"MA{period} {'上穿' if direction == 'long' else '下穿'} · {timeframe}",
                        event_key=f"ma{period}",
                        evidence={
                            "ma": {
                                "type": kind,
                                "period": period,
                                "value": round(ma[i], 6),  # type: ignore[arg-type]
                                "distance_atr": round((close - ma[i]) * sign / atr[i], 4),
                                "slope_atr_per_bar": round(slope / atr[i], 4)
                                if slope is not None
                                else None,
                            },
                            "price": {
                                "body_atr": round(body_atr, 4),
                                "close_location": round(close_location, 4),
                            },
                            "atr": {"period": atr_period, "value": round(atr[i], 6)},
                            "volume_ratio": round(volume_ratio, 4)
                            if volume_ratio is not None
                            else None,
                        },
                    )
                )
        return events


def build_ma_detectors() -> list[MaBreakoutDetector]:
    return [MaBreakoutDetector()]
