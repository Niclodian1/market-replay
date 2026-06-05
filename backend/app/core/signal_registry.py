from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class SignalHit:
    name: str
    direction: str
    timeframe: str
    score: float
    fired_at_ts: int
    fired_price: float
    payload: dict[str, Any]


class SignalDetector(Protocol):
    name: str
    display_name: str
    category: str
    description: str
    logic: str
    direction_support: list[str]
    params_schema: dict[str, Any]

    def detect(self, candles: list[dict[str, Any]], timeframe: str) -> list[SignalHit]:
        ...


class SignalRegistry:
    def __init__(self) -> None:
        self._detectors: dict[str, SignalDetector] = {}

    def register(self, detector: SignalDetector) -> None:
        if detector.name in self._detectors:
            raise ValueError(f"Signal detector already registered: {detector.name}")
        self._detectors[detector.name] = detector

    def get(self, name: str) -> SignalDetector:
        return self._detectors[name]

    def run_all(self, candles: list[dict[str, Any]], timeframe: str) -> list[SignalHit]:
        hits: list[SignalHit] = []
        for detector in self._detectors.values():
            hits.extend(detector.detect(candles, timeframe))
        return hits

    @property
    def names(self) -> list[str]:
        return sorted(self._detectors)


signal_registry = SignalRegistry()
