"""indicator-core — 指标与事件的唯一契约层（P0）。

设计见 ``docs/refactor/ARCHITECTURE_DESIGN_2026-10-22.md`` §2。

本包只做三件事：

1. 定义 ``IndicatorSpec``（指标是什么）与 ``Event``（一次触发）；
2. 提供 ``IndicatorRegistry``（注册 / 查询 / 运行）；
3. 通过 ``adapters`` 把既有引擎（缠论、MA、MACD…）归一化成 ``Event``。

它 **不依赖** FastAPI / SQLAlchemy / 数据库，任何消费方（看盘、练习、统计、
告警、实验）都只认这里的契约。
"""
from .registry import Detector, IndicatorRegistry, UnknownIndicator
from .spec import SCHEMA_VERSION, Event, IndicatorSpec, canonical_json, params_hash

__all__ = [
    "SCHEMA_VERSION",
    "Detector",
    "Event",
    "IndicatorRegistry",
    "IndicatorSpec",
    "UnknownIndicator",
    "canonical_json",
    "params_hash",
]
