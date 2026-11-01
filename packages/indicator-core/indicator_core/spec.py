"""指标与事件的数据契约。

只依赖标准库，方便被后端、脚本、未来的独立包复用。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Literal

SCHEMA_VERSION = "ev1"

Direction = Literal["long", "short"]


def canonical_json(value: Any) -> str:
    """稳定序列化：同一份参数永远得到同一个字符串（用于哈希 / 去重）。"""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )


def params_hash(params: dict[str, Any] | None) -> str:
    """参数指纹（8 位）。同一指标不同参数 = 不同事件命名空间。"""
    return hashlib.sha256(canonical_json(params or {}).encode("utf-8")).hexdigest()[:8]


@dataclass(frozen=True)
class IndicatorSpec:
    """指标注册项（静态描述，不含逻辑）。"""

    id: str
    label: str
    group: str
    timeframes: tuple[str, ...]
    warmup_bars: int = 0
    direction_support: tuple[Direction, ...] = ("long",)
    description: str = ""
    logic: str = ""
    params_schema: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.id or "." not in self.id:
            raise ValueError(f"指标 id 需要 '<group>.<name>' 形式: {self.id!r}")
        if not self.timeframes:
            raise ValueError(f"指标 {self.id} 必须声明支持的周期")
        if not self.direction_support:
            raise ValueError(f"指标 {self.id} 必须声明方向")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "group": self.group,
            "timeframes": list(self.timeframes),
            "warmup_bars": self.warmup_bars,
            "direction_support": list(self.direction_support),
            "description": self.description,
            "logic": self.logic,
            "params_schema": self.params_schema,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True)
class Event:
    """一次指标触发 —— 系统里唯一的事件格式。

    因果约束（构造时强制）：
    - ``bar_time`` 是信号所在 K 线；
    - ``confirmed_at`` 是「到这一刻才知道」的确认 K 线，必须 >= ``bar_time``；
    - ``first_seen_at`` 预留给严格「当时才知道」语义，默认等于 ``confirmed_at``。
    """

    indicator_id: str
    symbol: str
    timeframe: str
    bar_time: int
    confirmed_at: int
    direction: Direction
    price: float
    params_hash: str = ""
    state: str = "confirmed"
    strength: float | None = None
    title: str = ""
    event_key: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    first_seen_at: int | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name, value in (("bar_time", self.bar_time), ("confirmed_at", self.confirmed_at)):
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} 必须是正整数时间戳，收到 {value!r}")
        if self.confirmed_at < self.bar_time:
            raise ValueError(
                f"confirmed_at({self.confirmed_at}) 早于 bar_time({self.bar_time})，违反因果约束"
            )
        if not math.isfinite(self.price) or self.price <= 0:
            raise ValueError(f"price 必须是有限正数，收到 {self.price!r}")
        if self.direction not in ("long", "short"):
            raise ValueError(f"direction 必须是 long/short，收到 {self.direction!r}")

    @property
    def uid(self) -> str:
        """去重键：同指标、同品种、同周期、同 K 线、同参数 = 同一事件。"""
        parts = [
            self.indicator_id,
            self.symbol.upper(),
            self.timeframe,
            str(self.bar_time),
            self.params_hash or "",
        ]
        if self.event_key:
            parts.append(self.event_key)
        return "|".join(parts)

    @property
    def seen_at(self) -> int:
        return self.first_seen_at if self.first_seen_at is not None else self.confirmed_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "indicator_id": self.indicator_id,
            "symbol": self.symbol.upper(),
            "timeframe": self.timeframe,
            "bar_time": self.bar_time,
            "confirmed_at": self.confirmed_at,
            "first_seen_at": self.seen_at,
            "direction": self.direction,
            "price": self.price,
            "strength": self.strength,
            "state": self.state,
            "title": self.title,
            "event_key": self.event_key,
            "evidence": self.evidence,
            "params_hash": self.params_hash,
            "schema_version": self.schema_version,
        }
