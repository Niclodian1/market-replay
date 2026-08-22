"""M2 多周期融合 —— 端到端决策的数据契约（schema_version=m2-v1）。

仅作数据契约（对 evaluate().to_dict() 做严格 Pydantic 校验），
**不是** DB/API schema，不接入生产 monitor，不持久化、不发送。

第三轮复验整改（2026-08-13）：schema 与运行时语义严格等价——
  - 全模型 ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)：
    所有浮点字段禁止 NaN/Inf；nested evidence 同受约束；
  - evidence 用严格 typed 角色模型（context/setup/trigger 三键固定，
    extra forbid，数值 finite），不再用 dict[str, Any] 承载；
  - eligible 与三个 OK codes 严格等价：eligible=True iff reason_codes 恰为
    [ctx_ok, setup_ok, trigger_ok]；eligible=False 必须至少一个 reject code
    （不能全 OK / 空）；direction 等价不变；
  - eligible=True ⇒ trigger_event.reference_price 与
    trigger.market_state.features.close 在 1e-6 相对容差内（二者有限正数）、
    event_type/raw_direction 非空、timeframe=='4h'、normalized_direction==
    direction、signal_time==trigger.bar_time；
  - MarketState 状态字符串 Literal 化；ConfigHash 16 位 hex；config_hash 链
    一致（period==state==顶层 market_state）；JSON roundtrip 后仍通过。
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

Role = Literal["context", "setup", "trigger"]
Timeframe = Literal["1week", "1day", "4h"]
Direction = Literal["long", "short"]

# 与 app.core.multi_timeframe.ALL_REASON_CODES 保持一致（单一来源）
ReasonCode = Literal[
    "ctx_ok", "setup_ok", "trigger_ok",
    "ctx_not_ready", "ctx_sideways", "ctx_reject_direction",
    "setup_not_ready", "setup_inconsistent",
    "trigger_not_ready", "trigger_wrong_period", "trigger_stale",
    "trigger_bad_time", "trigger_bad_direction",
    "trigger_bad_reference", "trigger_bad_event_type",
    "trigger_payload_conflict",
]
VALID_REASON_CODES = frozenset(ReasonCode.__args__)  # type: ignore[attr-defined]

# 与 app.core.multi_timeframe.ROLE_TIMEFRAMES 一致（固定业务法则）
FIXED_ROLE_TIMEFRAMES = {"context": "1week", "setup": "1day", "trigger": "4h"}
OK_CODES = ("ctx_ok", "setup_ok", "trigger_ok")

HEX16 = r"^[0-9a-f]{16}$"

# 与 app.core.multi_timeframe.REFERENCE_TOLERANCE 一致
REFERENCE_TOLERANCE = 1e-6


class TrendOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    direction: Literal["up", "down", "sideways", "unknown"]
    strength: Literal["ranging", "weak", "strong", "unknown"]
    swing_score: int
    ma_direction_score: int
    price_position_score: int
    adx: Optional[float] = None
    reason: str


class VolatilityOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    atr_price: Optional[float] = None
    percentile: Optional[float] = None
    change_5: Optional[float] = None
    level: Literal["low", "normal", "high", "unknown"]
    phase: Literal["contraction", "transition", "expansion", "unknown"]
    previous_phase: Literal["contraction", "transition", "expansion", "unknown"]
    phase_age: int
    reason: str


class CandleOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    direction: Literal["bullish", "bearish", "neutral"]
    strength: Literal["weak", "normal", "strong", "unknown"]
    body_atr: Optional[float] = None
    range_atr: Optional[float] = None
    body_range: Optional[float] = None
    close_location_long: Optional[float] = None
    close_location_short: Optional[float] = None
    volume_state: str
    reason: str


class FeaturesOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    close: Optional[float] = None
    ma20: Optional[float] = None
    ma60: Optional[float] = None
    ma120: Optional[float] = None
    atr: Optional[float] = None
    ma20_prev2: Optional[float] = None
    ma60_prev2: Optional[float] = None
    ma120_prev2: Optional[float] = None


class MarketStateOut(BaseModel):
    """M1 MarketState.to_dict() 的严格镜像（extra forbid + strict + 无 NaN/Inf）。"""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    schema_version: str
    config_hash: str = Field(pattern=HEX16)
    bar_index: int
    bar_time: int
    ready: bool
    missing_reason: Optional[str] = None
    is_closed: bool
    trend: TrendOut
    volatility: VolatilityOut
    candle: CandleOut
    features: FeaturesOut
    reasons: list[str]


class AlignedPeriodOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    role: Role
    timeframe: Timeframe
    available: bool
    unavailable_reason: Optional[str] = None
    bar_time: Optional[int] = None
    bar_close_time: Optional[int] = None
    config_hash: str = Field(pattern=HEX16)
    market_state: Optional[MarketStateOut] = None
    reasons: list[str]


class TriggerEventOut(BaseModel):
    """Trigger 审计摘要（无原始 payload，只输出经校验字段）。"""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    event_type: str
    raw_direction: str
    normalized_direction: Optional[Direction] = None
    signal_time: Optional[int] = None
    reference_price: Optional[float] = None
    summary: Optional[str] = None
    timeframe: Optional[str] = None


class EvidenceVolOut(BaseModel):
    """单角色波动证据（有限数值；evidence 只写不门控）。"""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    level: str
    phase: str
    atr_price: Optional[float] = None
    percentile: Optional[float] = None
    change_5: Optional[float] = None


class EvidenceCandleOut(BaseModel):
    """单角色 K 线证据（有限数值）。"""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    direction: str
    strength: str
    body_atr: Optional[float] = None
    close_location_long: Optional[float] = None
    close_location_short: Optional[float] = None


class EvidenceVolatility(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    context: Optional[EvidenceVolOut] = None
    setup: Optional[EvidenceVolOut] = None
    trigger: Optional[EvidenceVolOut] = None


class EvidenceCandle(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    context: Optional[EvidenceCandleOut] = None
    setup: Optional[EvidenceCandleOut] = None
    trigger: Optional[EvidenceCandleOut] = None


class EvidenceVolume(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    context: Optional[str] = None
    setup: Optional[str] = None
    trigger: Optional[str] = None


class EvidenceOut(BaseModel):
    """波动/蜡烛/成交量证据：只写证据，不做硬门槛（M3 前不得宣称优势）。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    volatility: EvidenceVolatility
    candle: EvidenceCandle
    volume: EvidenceVolume


class ConfigHashOut(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    market_state: str = Field(pattern=HEX16)
    fusion: str = Field(pattern=HEX16)


class MarketFusionOut(BaseModel):
    """端到端融合决策契约（schema_version=m2-v1）。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: str = Field(pattern=r"^m2-v1$")
    symbol: str
    as_of: int = Field(ge=0)
    direction: Optional[Direction] = None
    eligible: bool
    context: AlignedPeriodOut
    setup: AlignedPeriodOut
    trigger: AlignedPeriodOut
    trigger_event: TriggerEventOut
    reason_codes: list[ReasonCode]
    explanations: list[str]
    config_hash: ConfigHashOut
    evidence: EvidenceOut

    @model_validator(mode="after")
    def _semantic_checks(self) -> "MarketFusionOut":
        # 三角色 role/timeframe 固定
        for role, tf in FIXED_ROLE_TIMEFRAMES.items():
            period = getattr(self, role)
            if period.role != role or period.timeframe != tf:
                raise ValueError(
                    f"{role} 角色/周期固定为 {role}/{tf}，收到 {period.role}/{period.timeframe}"
                )
        # reason_codes / explanations 等长
        if len(self.reason_codes) != len(self.explanations):
            raise ValueError(
                f"reason_codes({len(self.reason_codes)}) 与 "
                f"explanations({len(self.explanations)}) 必须等长"
            )
        # available=True ⇒ 完整状态链
        for role in FIXED_ROLE_TIMEFRAMES:
            period = getattr(self, role)
            if not period.available:
                continue  # available=False 不强求 state 为空（warmup 可携 state）
            if period.unavailable_reason is not None:
                raise ValueError(f"{role} available=True 时 unavailable_reason 必须为 None")
            if (period.bar_time is None or period.bar_close_time is None
                    or period.market_state is None):
                raise ValueError(
                    f"{role} available=True 时 bar_time/bar_close_time/market_state 必须非空"
                )
            ms = period.market_state
            if not ms.ready or not ms.is_closed:
                raise ValueError(
                    f"{role} available=True 时 market_state 必须 ready 且 is_closed"
                )
            if ms.bar_time != period.bar_time:
                raise ValueError(
                    f"{role} market_state.bar_time({ms.bar_time}) 必须等于 "
                    f"period.bar_time({period.bar_time})"
                )
            if not (period.config_hash == ms.config_hash == self.config_hash.market_state):
                raise ValueError(
                    f"{role} config_hash 链必须一致（period={period.config_hash}, "
                    f"state={ms.config_hash}, 顶层={self.config_hash.market_state}）"
                )
            if not (period.bar_close_time > period.bar_time):
                raise ValueError(f"{role} bar_close_time 必须大于 bar_time")
        # eligible 与三个 OK codes 严格等价（第三轮整改）
        codes = tuple(self.reason_codes)
        if self.eligible:
            if codes != OK_CODES:
                raise ValueError(
                    f"eligible=True iff reason_codes 恰为 {list(OK_CODES)}，"
                    f"收到 {self.reason_codes}"
                )
            if self.direction not in ("long", "short"):
                raise ValueError("eligible=True 时 direction 必须为 long/short")
            for role in FIXED_ROLE_TIMEFRAMES:
                if not getattr(self, role).available:
                    raise ValueError(f"eligible=True 时 {role} 必须 available")
            te = self.trigger_event
            if not te.event_type or not te.event_type.strip():
                raise ValueError("eligible=True 时 event_type 必须非空")
            if not te.raw_direction or not te.raw_direction.strip():
                raise ValueError("eligible=True 时 raw_direction 必须非空")
            if te.timeframe != "4h":
                raise ValueError("eligible=True 时 trigger_event.timeframe 必须为 '4h'")
            if te.normalized_direction != self.direction:
                raise ValueError(
                    f"eligible=True 时 normalized_direction({te.normalized_direction}) "
                    f"必须等于 direction({self.direction})"
                )
            if te.signal_time is None or te.signal_time != self.trigger.bar_time:
                raise ValueError(
                    f"eligible=True 时 signal_time({te.signal_time}) 必须等于 "
                    f"trigger.bar_time({self.trigger.bar_time})"
                )
            # reference_price 必须与 trigger close 一致（1e-6 相对容差，均有限正数）
            ref = te.reference_price
            close = (self.trigger.market_state.features.close
                     if self.trigger.market_state else None)
            if ref is None or close is None or ref <= 0 or close <= 0:
                raise ValueError(
                    "eligible=True 时 reference_price 与 trigger.close 必须为有限正数"
                )
            if abs(ref - close) > REFERENCE_TOLERANCE * abs(close):
                raise ValueError(
                    f"eligible=True 时 reference_price({ref}) 必须与 "
                    f"trigger.close({close}) 一致（1e-6 相对容差）"
                )
        else:
            if self.direction is not None:
                raise ValueError("eligible=False 时 direction 必须为 None")
            if not codes or all(c in OK_CODES for c in codes):
                raise ValueError(
                    "eligible=False 时 reason_codes 必须至少一个 reject code"
                    f"（不能全 OK/空），收到 {self.reason_codes}"
                )
        return self


def validate_decision(decision_dict: dict[str, Any]) -> MarketFusionOut:
    """严格校验端到端 dict（evaluate(...).to_dict() 必须通过）。"""
    return MarketFusionOut.model_validate(decision_dict)
