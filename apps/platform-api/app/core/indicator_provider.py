"""把 backend 的缠论服务适配成 ``indicator_core`` 的 provider，并构建全局注册表。

这里是「引擎」与「契约」之间唯一的粘合层：

- ``ServiceChanlunProvider`` 负责调用 ``chanlun_service.analyze_symbol``（含周期名映射）
  并按 (symbol, timeframe, bar 数, 末根时间) 做一次短缓存 —— 同一请求里 10 个缠论指标
  只会真正跑一次引擎。
- ``get_registry()`` 返回进程内单例注册表；后续 MA/MACD/结构 adapter 也在这里注册。
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Sequence

from app.core import chanlun_service
from app.indicator_core import IndicatorRegistry
from app.core.divergence_detectors import macd_extreme_divergence_events
from app.indicator_core.adapters import (
    ChanlunProvider,
    build_chanlun_detectors,
    build_ma_detectors,
    build_macd_detectors,
)

# 后端周期名 -> chanlun-core 引擎周期名
_ENGINE_TF = {"1day": "1D", "4h": "4H", "1h": "1H"}

_DEFAULT_MAX_ENTRIES = 8


class ServiceChanlunProvider(ChanlunProvider):
    """缠论结构 provider（带小容量 LRU，避免一次请求重复跑引擎）。"""

    def __init__(self, max_entries: int = _DEFAULT_MAX_ENTRIES) -> None:
        self._max_entries = max_entries
        self._cache: OrderedDict[tuple, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def analyze(
        self, symbol: str, timeframe: str, bars: Sequence[dict[str, Any]]
    ) -> dict[str, Any]:
        if timeframe not in _ENGINE_TF:
            raise ValueError(f"缠论不支持周期 {timeframe!r}")
        if not bars:
            raise ValueError("bars 为空，无法分析")
        key = (
            symbol.upper(),
            timeframe,
            len(bars),
            int(bars[-1].get("time") or 0),
        )
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached

        levels = chanlun_service.analyze_symbol({timeframe: list(bars)})
        level = levels.get(_ENGINE_TF[timeframe])
        if not level:
            raise RuntimeError(f"缠论分析无输出: {symbol} {timeframe}")

        with self._lock:
            self._cache[key] = level
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_entries:
                self._cache.popitem(last=False)
        return level

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


_provider = ServiceChanlunProvider()
_registry: IndicatorRegistry | None = None
_registry_lock = threading.Lock()


def get_provider() -> ServiceChanlunProvider:
    return _provider


def get_registry() -> IndicatorRegistry:
    """进程内单例注册表（懒加载）。"""
    global _registry
    if _registry is not None:
        return _registry
    with _registry_lock:
        if _registry is None:
            registry = IndicatorRegistry()
            detectors = [
                *build_chanlun_detectors(_provider),
                *build_ma_detectors(),
                *build_macd_detectors(macd_extreme_divergence_events),
            ]
            for detector in detectors:
                registry.register(detector)
            _registry = registry
    return _registry
