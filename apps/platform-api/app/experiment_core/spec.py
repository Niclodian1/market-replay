"""实验 spec：声明式、可校验、可哈希。

一个实验是「入场条件 + 出场条件 + 成本/仓位 + 验证方式」的 JSON，
不是一段脚本。所有能力来自 ``indicator_core`` 已注册的指标。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from app.indicator_core.spec import params_hash

SPEC_SCHEMA_VERSION = "exp1"

_CONDITION = {
    "type": "object",
    "properties": {
        "indicator": {"type": "string"},
        "params": {"type": "object"},
    },
    "required": ["indicator"],
    "additionalProperties": False,
}

_EXIT = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "indicator": {"type": "string"},
                "params": {"type": "object"},
            },
            "required": ["indicator"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "type": {"enum": ["stop", "target", "time"]},
                "atr": {"type": "number", "exclusiveMinimum": 0},
                "r": {"type": "number", "exclusiveMinimum": 0},
                "bars": {"type": "integer", "minimum": 1},
            },
            "required": ["type"],
            "additionalProperties": False,
        },
    ]
}

SPEC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "schema_version": {"const": SPEC_SCHEMA_VERSION},
        "name": {"type": "string", "minLength": 1, "maxLength": 120},
        "universe": {
            "type": "object",
            "properties": {
                "symbols": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "timeframes": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            },
            "required": ["symbols", "timeframes"],
            "additionalProperties": False,
        },
        "period": {
            "type": "object",
            "properties": {
                "from": {"type": "string"},
                "to": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "entry": {
            "type": "object",
            "properties": {
                "all": {"type": "array", "items": _CONDITION, "minItems": 1},
                "any": {"type": "array", "items": _CONDITION, "minItems": 1},
            },
            "minProperties": 1,
            "additionalProperties": False,
        },
        "exit": {
            "type": "object",
            "properties": {
                "any": {"type": "array", "items": _EXIT, "minItems": 1},
            },
            "required": ["any"],
            "additionalProperties": False,
        },
        "sizing": {
            "type": "object",
            "properties": {
                "mode": {"enum": ["fixed_risk"]},
                "risk_pct": {"type": "number", "exclusiveMinimum": 0, "maximum": 100},
            },
            "additionalProperties": False,
        },
        "costs": {
            "type": "object",
            "properties": {
                "fee_bps": {"type": "number", "minimum": 0},
                "slippage_bps": {"type": "number", "minimum": 0},
            },
            "additionalProperties": False,
        },
        "validation": {
            "type": "object",
            "properties": {
                "split": {"enum": ["holdout", "none"]},
                "dev_share": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1},
                "min_trades": {"type": "integer", "minimum": 1},
            },
            "additionalProperties": False,
        },
    },
    "required": ["name", "universe", "entry", "exit"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ExitRule:
    kind: str  # indicator | stop | target | time
    indicator: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    atr: float = 1.5
    r: float = 2.0
    bars: int = 20


@dataclass(frozen=True)
class ExperimentSpec:
    name: str
    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]
    entry_all: tuple[tuple[str, dict[str, Any]], ...]
    entry_any: tuple[tuple[str, dict[str, Any]], ...]
    exits: tuple[ExitRule, ...]
    risk_pct: float = 1.0
    fee_bps: float = 10.0
    slippage_bps: float = 5.0
    dev_share: float = 0.7
    min_trades: int = 30
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def spec_hash(self) -> str:
        return params_hash(self.raw)

    @property
    def stop_atr(self) -> float:
        for rule in self.exits:
            if rule.kind == "stop":
                return rule.atr
        return 1.5

    @property
    def target_r(self) -> float:
        for rule in self.exits:
            if rule.kind == "target":
                return rule.r
        return 2.0

    @property
    def time_bars(self) -> int:
        for rule in self.exits:
            if rule.kind == "time":
                return rule.bars
        return 20

    @property
    def exit_indicators(self) -> tuple[tuple[str, dict[str, Any]], ...]:
        return tuple(
            (rule.indicator, rule.params)
            for rule in self.exits
            if rule.kind == "indicator" and rule.indicator
        )


def validate_spec(raw: dict[str, Any]) -> None:
    jsonschema.validate(raw, SPEC_SCHEMA)


def parse_spec(raw: dict[str, Any]) -> ExperimentSpec:
    """校验并补默认值；非法 spec 直接抛 ``jsonschema.ValidationError`` / ``ValueError``。"""
    payload = dict(raw)
    payload.setdefault("schema_version", SPEC_SCHEMA_VERSION)
    validate_spec(payload)

    def conditions(key: str) -> tuple[tuple[str, dict[str, Any]], ...]:
        block = payload["entry"].get(key) or []
        return tuple((item["indicator"], dict(item.get("params") or {})) for item in block)

    exits: list[ExitRule] = []
    for item in payload["exit"]["any"]:
        kind = item.get("type") or "indicator"
        exits.append(
            ExitRule(
                kind=kind,
                indicator=item.get("indicator"),
                params=dict(item.get("params") or {}),
                atr=float(item.get("atr", 1.5)),
                r=float(item.get("r", 2.0)),
                bars=int(item.get("bars", 20)),
            )
        )

    fixed = [rule for rule in exits if rule.kind in ("stop", "target", "time")]
    if not fixed:
        exits.extend(
            [
                ExitRule(kind="stop", atr=1.5),
                ExitRule(kind="target", r=2.0),
                ExitRule(kind="time", bars=20),
            ]
        )

    sizing = payload.get("sizing") or {}
    costs = payload.get("costs") or {}
    validation = payload.get("validation") or {}
    return ExperimentSpec(
        name=payload["name"],
        symbols=tuple(payload["universe"]["symbols"]),
        timeframes=tuple(payload["universe"]["timeframes"]),
        entry_all=conditions("all"),
        entry_any=conditions("any"),
        exits=tuple(exits),
        risk_pct=float(sizing.get("risk_pct", 1.0)),
        fee_bps=float(costs.get("fee_bps", 10.0)),
        slippage_bps=float(costs.get("slippage_bps", 5.0)),
        dev_share=float(validation.get("dev_share", 0.7)),
        min_trades=int(validation.get("min_trades", 30)),
        raw=payload,
    )


def spec_to_json(spec: ExperimentSpec) -> str:
    return json.dumps(spec.raw, ensure_ascii=False, sort_keys=True)
