"""MACD 背离 adapter（``macd.divergence``）。

复用 ``app.core.divergence_detectors.macd_extreme_divergence_events`` 的既有规则，
只做「事件契约」转换：

- ``t``（极值 K 线）→ ``bar_time``
- ``confirm_t``（极值 +3 根确认）→ ``confirmed_at`` —— 这条指标天生「滞后 3 根」，
  正好是因果契约要表达的信息；
- ``pending``（确认根尚未走完）默认丢弃，避免统计混入未确认信号。
"""
from __future__ import annotations

from typing import Any, Callable, Sequence

from ..spec import Event, IndicatorSpec, params_hash

# 由宿主注入（backend 侧实现见 app.core.divergence_detectors）
DivergenceScan = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]

_SUPPORTED_TFS = ("15min", "1h", "4h", "1day", "1week")

_PARAMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "include_pending": {
            "type": "boolean",
            "default": False,
            "title": "包含未确认",
            "description": "确认根尚未走完的背离（仅供观察，不应进入统计）",
        },
        "min_strength": {
            "type": "number",
            "default": 0.0,
            "minimum": 0,
            "maximum": 1,
            "title": "最小背离强度",
        },
    },
    "additionalProperties": False,
}

_SPEC = IndicatorSpec(
    id="macd.divergence",
    label="MACD 背离",
    group="macd",
    timeframes=_SUPPORTED_TFS,
    warmup_bars=60,
    direction_support=("long", "short"),
    description="柱极值背离：动能相对减弱；极值 +3 根收盘确认",
    logic="同向柱段极值对比（间隔 ≥3 根反向柱），strength=1-|hist|/|hist_prev|",
    params_schema=_PARAMS_SCHEMA,
)


class MacdDivergenceDetector:
    spec = _SPEC

    def __init__(self, scan: DivergenceScan) -> None:
        self._scan = scan

    def detect(
        self,
        bars: Sequence[dict[str, Any]],
        symbol: str,
        timeframe: str,
        params: dict[str, Any],
    ) -> list[Event]:
        include_pending = bool(params.get("include_pending", False))
        min_strength = float(params.get("min_strength", 0.0))
        if not 0 <= min_strength <= 1:
            raise ValueError("min_strength 必须在 [0, 1] 之间")
        if len(bars) < self.spec.warmup_bars:
            return []

        raw = self._scan(list(bars))
        p_hash = params_hash(
            {"include_pending": include_pending, "min_strength": min_strength}
        )
        by_time = {int(bar["time"]): bar for bar in bars}

        events: list[Event] = []
        for item in raw:
            state = str(item.get("state") or "confirmed")
            if state != "confirmed" and not include_pending:
                continue
            strength = float(item.get("strength") or 0.0)
            if strength < min_strength:
                continue
            bar_time = int(item["t"])
            candle = by_time.get(bar_time)
            if candle is None:
                continue
            merged = {
                "state": state,
                "strength": strength,
                "strength_tier": item.get("strength_tier"),
                # pending 时 confirm_t 只是「预计确认时间」，用 max 保证契约不越界
                "confirmed_at": max(bar_time, int(item.get("confirm_t") or bar_time)),
            }
            events.append(
                Event(
                    indicator_id=self.spec.id,
                    symbol=symbol.upper(),
                    timeframe=timeframe,
                    bar_time=bar_time,
                    confirmed_at=merged["confirmed_at"],
                    direction="long" if item.get("side") == "buy" else "short",
                    price=float(candle["close"]),
                    params_hash=p_hash,
                    state=state,
                    strength=round(strength, 4),
                    title=f"MACD {'底' if item.get('side') == 'buy' else '顶'}背离 · {timeframe}",
                    evidence={
                        "prev_extreme": item.get("prev_extreme"),
                        "alert_t": item.get("alertT"),
                        "height_ratio": item.get("height_ratio"),
                        "strength": strength,
                        "strength_tier": item.get("strength_tier"),
                    },
                )
            )
        return events


def build_macd_detectors(scan: DivergenceScan) -> list[MacdDivergenceDetector]:
    return [MacdDivergenceDetector(scan)]
