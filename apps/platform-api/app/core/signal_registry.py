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
    # 策略类型: "trend" | "momentum" | "reversal" | "indicator"
    # indicator = 原始指标信号（MA突破/RSI/MACD等），不受 regime 过滤
    strategy_type: str
    # 该策略在哪些 Regime ID 下允许触发 (空集 = 不限，用于 indicator 类型)
    valid_regimes: set[int]

    def detect(self, candles: list[dict[str, Any]], timeframe: str) -> list[SignalHit]:
        ...

    def signal_array(self, candles: list[dict[str, Any]], params: dict[str, Any]) -> list[int]:
        """返回与 candles 等长的每根 K 线点信号(1=买/-1=卖/0=无)，供回测引擎组合与撮合使用。

        与 detect() 共享同一份底层信号数学，区别仅在输出形态：
        detect() 产出离散事件(SignalHit)，signal_array() 产出逐根数组。
        """
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
