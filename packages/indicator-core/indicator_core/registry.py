"""指标注册表：注册、查询、运行。

刻意保持薄——不算任何指标，只负责调度与契约校验。
"""
from __future__ import annotations

from typing import Any, Iterable, Protocol, Sequence, runtime_checkable

from .spec import Event, IndicatorSpec


class UnknownIndicator(KeyError):
    """请求了未注册的指标。"""


@runtime_checkable
class Detector(Protocol):
    """指标实现方需要满足的最小接口。"""

    spec: IndicatorSpec

    def detect(
        self,
        bars: Sequence[dict[str, Any]],
        symbol: str,
        timeframe: str,
        params: dict[str, Any],
    ) -> list[Event]:
        ...


class IndicatorRegistry:
    def __init__(self, detectors: Iterable[Detector] = ()) -> None:
        self._detectors: dict[str, Detector] = {}
        for detector in detectors:
            self.register(detector)

    # ---- 注册 / 查询 ----------------------------------------------------
    def register(self, detector: Detector) -> None:
        spec = detector.spec
        if spec.id in self._detectors:
            raise ValueError(f"指标重复注册: {spec.id}")
        self._detectors[spec.id] = detector

    def get(self, indicator_id: str) -> Detector:
        try:
            return self._detectors[indicator_id]
        except KeyError as exc:
            raise UnknownIndicator(f"未注册的指标: {indicator_id}") from exc

    @property
    def names(self) -> list[str]:
        return sorted(self._detectors)

    @property
    def specs(self) -> list[IndicatorSpec]:
        return [self._detectors[name].spec for name in self.names]

    def catalog(self) -> list[dict[str, Any]]:
        return [spec.to_dict() for spec in self.specs]

    # ---- 运行 ------------------------------------------------------------
    def run(
        self,
        indicator_id: str,
        bars: Sequence[dict[str, Any]],
        symbol: str,
        timeframe: str,
        params: dict[str, Any] | None = None,
    ) -> list[Event]:
        detector = self.get(indicator_id)
        self._check_supported(detector.spec, timeframe)
        events = detector.detect(bars, symbol, timeframe, params or {})
        return sorted(events, key=lambda e: (e.confirmed_at, e.bar_time, e.indicator_id))

    def run_many(
        self,
        indicator_ids: Iterable[str],
        bars: Sequence[dict[str, Any]],
        symbol: str,
        timeframe: str,
        params: dict[str, dict[str, Any]] | None = None,
    ) -> list[Event]:
        """按 uid 去重后返回，供「一次请求喂饱图表」使用。"""
        params = params or {}
        seen: set[str] = set()
        out: list[Event] = []
        for indicator_id in indicator_ids:
            for event in self.run(
                indicator_id, bars, symbol, timeframe, params.get(indicator_id)
            ):
                if event.uid in seen:
                    continue
                seen.add(event.uid)
                out.append(event)
        return sorted(out, key=lambda e: (e.confirmed_at, e.bar_time, e.indicator_id))

    def run_all(
        self,
        bars: Sequence[dict[str, Any]],
        symbol: str,
        timeframe: str,
        params: dict[str, dict[str, Any]] | None = None,
    ) -> list[Event]:
        return self.run_many(self.names, bars, symbol, timeframe, params)

    @staticmethod
    def _check_supported(spec: IndicatorSpec, timeframe: str) -> None:
        if timeframe not in spec.timeframes:
            raise ValueError(
                f"指标 {spec.id} 不支持周期 {timeframe!r}，支持: {list(spec.timeframes)}"
            )
