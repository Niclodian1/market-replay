"""M3B 影子运行时 —— 数据契约与严格校验（schema_version=m3b-v1）。

职责（不触碰 V1）：
  - 读写 JSON 一律 allow_nan=False + sort_keys canonical；写库前必须通过
    既有严格 validator：M2 决策 → ``app.schemas.market_fusion.validate_decision``、
    M3A 标签 → ``app.schemas.outcome_labeling.validate_outcome_label``；
  - 错误脱敏截断：不把 webhook/token/secret 写进 shadow 表；
  - ``ShadowStatusOut`` 只输出汇总/心跳/30 天与 100 唯一 eligible 进度，
    绝不输出原始 payload/秘密（CLI status 与 heartbeat 错误共用）。

本文件是纯数据契约，不读库、不发送、不导入任何 sender。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "m3b-v1"
HEX16 = r"^[0-9a-f]{16}$"

# 状态机：pending → labeled → completed；rejected（decision 不 eligible）；
# failed（可退避重试）；failure_stage ∈ {decision, label}。
STATUS_PENDING = "pending"
STATUS_REJECTED = "rejected"
STATUS_LABELED = "labeled"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
VALID_STATUSES = (STATUS_PENDING, STATUS_REJECTED, STATUS_LABELED, STATUS_COMPLETED, STATUS_FAILED)
# 失败阶段：decision（取数/计算失败，decision 字段可空）；label（标签失败，不得丢 decision）
STAGE_DECISION = "decision"
STAGE_LABEL = "label"

# 错误脱敏：常见秘密形态（webhook URL / token / secret / password）一律打码
_SECRET_PATTERNS = (
    re.compile(r"https?://\S*(?:discord|slack|feishu|webhook)\S*", re.IGNORECASE),
    re.compile(r"(?:token|secret|password|api[_-]?key|webhook[_-]?url)\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\b[0-9a-f]{40,}\b", re.IGNORECASE),  # 长 hex 密钥
)
MAX_ERROR_LEN = 300


def sanitize_error(message: str | Exception | None) -> str | None:
    """错误脱敏截断：None → None；其余去掉秘密形态并截断到 300 字符。"""
    if message is None:
        return None
    text = str(message)
    for pat in _SECRET_PATTERNS:
        text = pat.sub("<redacted>", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_ERROR_LEN:
        text = text[:MAX_ERROR_LEN] + "…"
    return text or None


def canonical_json(value: Any) -> str:
    """canonical JSON：sort_keys + allow_nan=False（含 NaN/Inf/不可序列化 → 受控 ValueError）。"""
    return json.dumps(value, sort_keys=True, allow_nan=False, ensure_ascii=False)


def digest16(payload: str) -> str:
    """sha256 前 16 位（与 M2/M3A digest 同源）。"""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class ShadowEventRow(BaseModel):
    """m3_shadow_events 行摘要（status/CLI 使用；不携带原始 payload/秘密）。

    读库后的 decision_json/label_json 只在回放（run-once/dry-run）时重新
    经过既有严格 validator，status 汇总不解析 payload 内容。
    """

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    id: int = Field(ge=1)
    source_dedupe_key: str = Field(min_length=1, max_length=240)
    source_event_id: int = Field(ge=1)
    symbol: str = Field(min_length=1, max_length=30)
    timeframe: str = Field(pattern=r"^4h$")
    signal_time: int = Field(ge=0)
    decision_digest: Optional[str] = Field(default=None, pattern=HEX16)
    eligible: Optional[bool] = None
    status: Literal["pending", "rejected", "labeled", "completed", "failed"]
    failure_stage: Optional[Literal["decision", "label"]] = None
    attempts: int = Field(ge=0)
    next_retry_at: Optional[int] = Field(default=None, ge=0)
    last_error: Optional[str] = Field(default=None, max_length=MAX_ERROR_LEN + 8)
    label_as_of: Optional[int] = Field(default=None, ge=0)
    maturity_window: Optional[Literal[5, 10, 20, 40]] = None
    next_label_at: Optional[int] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _state_consistency(self) -> "ShadowEventRow":
        # failure_stage 只在 failed；failed 必须带阶段（与 DB CHECK 一致）
        if (self.failure_stage is None) != (self.status != "failed"):
            raise ValueError(
                f"failure_stage 与 status 不一致: status={self.status} "
                f"failure_stage={self.failure_stage}（仅 failed 可带阶段）"
            )
        # decision 失败态：digest/eligible 必须为空；其余状态必须完整
        if self.failure_stage != "decision" and self.decision_digest is None:
            raise ValueError(
                f"非 decision 失败态必须决策完整（status={self.status}）"
            )
        if self.failure_stage == "decision" and (
            self.decision_digest is not None or self.eligible is not None
        ):
            raise ValueError("decision 失败态不得携带 decision 字段")
        # eligible 与状态一致（与 DB CHECK / validate_row_content 三层统一）
        if self.status == "failed" and self.failure_stage == "decision":
            if self.label_as_of is not None or self.maturity_window is not None:
                raise ValueError("failed/decision 不得携带 label")
        elif self.status == "rejected":
            if self.eligible is not False:
                raise ValueError("rejected 必须 eligible=false")
            if self.label_as_of is not None or self.maturity_window is not None:
                raise ValueError("rejected 不得携带 label")
        else:
            if self.eligible is not True:
                raise ValueError(f"{self.status} 必须 eligible=true")
        # labeled 真实 maturity<40；completed maturity=40
        if self.status == "labeled" and (
            self.maturity_window is None or self.maturity_window >= 40
        ):
            raise ValueError("labeled 必须真实 maturity<40")
        if self.status == "completed" and self.maturity_window != 40:
            raise ValueError("completed 必须 maturity=40")
        # 非 failed：attempts=0、error 空；rejected/completed 不得有 next_retry_at
        if self.status != "failed":
            if self.attempts != 0:
                raise ValueError(f"非 failed（{self.status}）必须 attempts=0")
            if self.last_error is not None:
                raise ValueError(f"非 failed（{self.status}）不得有 last_error")
            if self.status in ("rejected", "completed") and self.next_retry_at is not None:
                raise ValueError(f"{self.status} 不得有 next_retry_at")
        else:
            # failed 三层契约：attempts>=1、last_error 非空白（btrim）、next_retry_at 非空
            if self.attempts < 1:
                raise ValueError("failed 必须 attempts>=1")
            if self.next_retry_at is None:
                raise ValueError("failed 必须 next_retry_at 非空")
            if not self.last_error or not self.last_error.strip():
                raise ValueError("failed 必须 last_error 非空白")
        # label 存在 ⇒ label_as_of / maturity_window 存在
        # （label_json 不进入行摘要，用 maturity_window 推导约束）
        if self.maturity_window is not None and self.label_as_of is None:
            raise ValueError("maturity_window 非空时 label_as_of 必须存在")
        return self


class HeartbeatOut(BaseModel):
    """m3_shadow_runtime 心跳输出（CLI status）。"""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    exists: bool
    started_at: Optional[int] = Field(default=None, ge=0)
    last_run_at: Optional[int] = Field(default=None, ge=0)
    last_run_ok: Optional[bool] = None
    last_error: Optional[str] = Field(default=None, max_length=MAX_ERROR_LEN + 8)


class ShadowCountsOut(BaseModel):
    """m3_shadow_events 计数（CLI status；不解析 payload）。"""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    total: int = Field(ge=0)
    eligible: int = Field(ge=0)
    rejected: int = Field(ge=0)
    pending: int = Field(ge=0)
    labeled: int = Field(ge=0)
    completed: int = Field(ge=0)
    failed: int = Field(ge=0)
    decision_failed: int = Field(ge=0)
    label_failed: int = Field(ge=0)
    unique_eligible_digests: int = Field(ge=0)


class ShadowStatusOut(BaseModel):
    """CLI status 汇总：只输出汇总/心跳/30 天与 100 唯一 eligible 进度。"""

    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    enabled: bool
    symbols: list[str]
    timeframe: str
    max_bars: int
    heartbeat: HeartbeatOut
    counts: ShadowCountsOut
    # 30 天门槛（M3B 完成门槛之一）：从 heartbeat.started_at 起算
    days_elapsed: float = Field(ge=0)
    days_remaining_30: float = Field(ge=0)
    # ≥100 唯一 eligible decision_digest 进度
    unique_eligible_digests: int = Field(ge=0)
    unique_eligible_remaining: int = Field(ge=0)
    # 门槛未完成标记（任务书：本轮不伪造满 30 天/100 事件）
    milestone_reached: bool = False
