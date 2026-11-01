"""指标 adapter：把既有引擎翻译成统一 ``Event``。"""
from .chanlun import ChanlunProvider, build_chanlun_detectors
from .ma import MaBreakoutDetector, build_ma_detectors
from .macd import DivergenceScan, MacdDivergenceDetector, build_macd_detectors

__all__ = [
    "ChanlunProvider",
    "DivergenceScan",
    "MaBreakoutDetector",
    "MacdDivergenceDetector",
    "build_chanlun_detectors",
    "build_ma_detectors",
    "build_macd_detectors",
]
