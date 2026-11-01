"""M3B 安全影子运行时 —— V2 影子决策/标注的持久化编排（2026-08-14 主审整改）。

目标（M3B-A 任务书 + 主审整改）：
  - 用独立 worker 读取已持久化的 4H AlertEvent（不改 monitor_engine），
    让 M2 决策与 M3A 标签开始真实积累，但绝不改变 V1 告警/发送；
  - 拒绝决策也持久化；eligible 才排 M3A 标签；
  - 标签仅在 5/10/20/40 下一闭合边界（= signal_time + (w+1)*step）到期时
    更新，40 真实成熟后停止；maturity_window = 标签中实际成熟最大值
    （无则 None，不虚报）；重复/崩溃重跑不得重复计数或改写已完成结果；
  - 同 symbol+signal_time 复用同周期取数；M2 as_of = signal_time + 14400，
    只用该时点可见周/日/4H；
  - 不建 V1 外键；不发送；错误脱敏截断。

主审整改要点（2026-08-14）：
  1. 分页饥饿：fetch_candidates 在 LIMIT 前用 NOT EXISTS（anti-join）排除
     已处理源，>limit 两轮必须吃下一页；内存 repo 同口径。
  2. decision 摄取单事件隔离 + 持久化重试：failure_stage ∈ {decision, label}；
     decision/load 失败以 source_dedupe_key 唯一行落库（source 快照 + 脱敏
     错误 + attempts + next_retry_at，decision 字段允许为空）；到期重试同一
     行成功后补全严格校验的 decision 进入 rejected/pending；一个坏源不阻塞
     同轮后续好源；达 max_attempts 停止；label 失败不得丢 decision。
  3. production_market_loader 把 end_ts **精确**作为 provider 的 before 传入
     （before 是开盘时点上界；不是 end_ts+duration），保留 close_time<=end_ts
     二次过滤与最后 max_bars 截断，outputsize 多取少量覆盖边界；未来追加
     bar 一律被二次过滤（spy 断言 before 相等 + 未来追加不变）。
  4. scheduler 兜底写 heartbeat 后必须 commit（主路径强制抛 + patch
     app.db.session.AsyncSessionLocal 可观测 fake，commit 恰好 1 次且不冒泡）。
  5. 标签成熟真实性：maturity_window 必须等于实际 matured 最大值（无则
     None）；只有 40 真实 matured 才 completed；到期窗口缺根/未收盘保持
     可重试该窗口，不跳过不虚报。
  6. Sql repo 用 core.max_attempts（不硬编码）；ShadowCore 参数语义校验
     （timeframe 固定 4h、max_bars/limits/retry/max_attempts 合理非负）。
  7. DB CHECK + ShadowEventRow + validate_row_content 三层状态一致性统一
     （failed/decision 决策五字段全 NULL 且 label 空；rejected eligible=false
     且无 label；pending/labeled/completed/failed-label eligible=true；labeled
     真实 maturity<40；completed maturity=40；非 failed attempts=0 且 error
     空）；label_bound 允许 pending censored（label_json+label_as_of 非空、
     maturity NULL），仍拒绝 maturity 非空却无 label；读回交叉验证
     label.decision_digest==行 digest、label_as_of 一致、实际成熟最大值==行
     maturity_window、reasons 与 decision codes/explanations 一致、canonical
     字符串契约。
  8. run_cycle_safe/production commit、V1 隔离、禁 sender、disabled 零
     DB/网络仍成立。
  9. 缺数据/部分成熟不算 failed：update_label 支持 next_retry_at，0/部分成熟
     未到目标窗时设 now+backoff（label_retry_delay_seconds），list_label_due
     让 pending/labeled 尊重 backoff（不热循环、不饿死后排），成功推进清空。

时间契约（继承 M0/M1/M2/M3A）：
  - bar.time 为开盘 epoch 秒；close_time = time + duration（4h=14400）；
  - 只消费 close_time <= 截止 的已收盘 bar；追加未来 bar 不得改变
    已产生的 decision digest / 已成熟 label。

本模块禁止导入任何 sender（notification_channels/discord/feishu/webhook）；
V1 隔离：run_cycle_safe 永不抛出，异常只写 heartbeat.last_error。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import settings
from app.core.market_state import DEFAULT_MARKET_STATE_CONFIG, MarketStateConfig
from app.core.multi_timeframe import (
    DEFAULT_MULTI_TIMEFRAME_CONFIG,
    TIMEFRAME_DURATIONS,
    MultiTimeframeConfig,
    evaluate,
)
from app.core.outcome_labeling import (
    WINDOWS,
    decision_digest,
    label_decision,
)
from app.models.models import AlertEvent, M3ShadowEvent, M3ShadowRuntime
from app.schemas.market_fusion import validate_decision
from app.schemas.m3_shadow_runtime import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_LABELED,
    STATUS_PENDING,
    STATUS_REJECTED,
    STAGE_DECISION,
    STAGE_LABEL,
    canonical_json,
    sanitize_error,
)
from app.schemas.outcome_labeling import validate_outcome_label

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "m3b-v1"
HEARTBEAT_ID = 1
STEP_4H = TIMEFRAME_DURATIONS["4h"]  # 14400 秒
# 窗口 w 的成熟边界 = signal_time + (w+1)*step（第 w 根未来 bar 收盘时刻，
# 与 M3A 闭合公式一致）
WINDOW_BOUNDARY_OFFSETS: dict[int, int] = {w: (w + 1) * STEP_4H for w in WINDOWS}
DEFAULT_RETRY_DELAY_SECONDS = 300
DEFAULT_MAX_ATTEMPTS = 6
DEFAULT_INGEST_LIMIT = 200
DEFAULT_LABEL_LIMIT = 200
DEFAULT_MAX_BARS = 700
# 缺数据/部分成熟 backoff（防热循环：正常缺根不算 failed，但不得每分钟重试）
DEFAULT_LABEL_RETRY_DELAY_SECONDS = 900
# before 透传时多取的少量根数（覆盖 provider 边界语义差异）
BEFORE_PADDING_BARS = 16
FIXED_TIMEFRAME = "4h"


# ═══════════════════════════════════════════════════════════════════════════════
#  配置快照
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ShadowCore:
    """影子运行时配置快照（frozen；symbols 大写规范化；参数语义强校验）。"""

    enabled: bool = True
    symbols: frozenset[str] = frozenset({"BINANCE:BTCUSDT"})
    timeframe: str = FIXED_TIMEFRAME
    max_bars: int = 700
    market_state_config: MarketStateConfig = DEFAULT_MARKET_STATE_CONFIG
    fusion_config: MultiTimeframeConfig = DEFAULT_MULTI_TIMEFRAME_CONFIG
    retry_delay_seconds: int = DEFAULT_RETRY_DELAY_SECONDS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    ingest_limit: int = DEFAULT_INGEST_LIMIT
    label_limit: int = DEFAULT_LABEL_LIMIT
    label_retry_delay_seconds: int = DEFAULT_LABEL_RETRY_DELAY_SECONDS
    label_windows: tuple[int, ...] = WINDOWS
    step_4h: int = STEP_4H

    def __post_init__(self) -> None:
        # 语义校验：避免错误配置静默运行（主审整改 6）
        if self.timeframe != FIXED_TIMEFRAME:
            raise ValueError(
                f"timeframe 固定为 {FIXED_TIMEFRAME!r}（crypto UTC），收到 {self.timeframe!r}"
            )
        if self.step_4h != STEP_4H:
            raise ValueError(f"step_4h 固定为 {STEP_4H}（4h 秒数），收到 {self.step_4h!r}")
        for name, val in (
            ("max_bars", self.max_bars),
            ("ingest_limit", self.ingest_limit),
            ("label_limit", self.label_limit),
            ("max_attempts", self.max_attempts),
        ):
            if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
                raise ValueError(f"{name} 必须为正整数，收到 {val!r}")
        if isinstance(self.retry_delay_seconds, bool) or not isinstance(
            self.retry_delay_seconds, int
        ) or self.retry_delay_seconds < 0:
            raise ValueError(
                f"retry_delay_seconds 必须为非负整数，收到 {self.retry_delay_seconds!r}"
            )
        if isinstance(self.label_retry_delay_seconds, bool) or not isinstance(
            self.label_retry_delay_seconds, int
        ) or self.label_retry_delay_seconds < 0:
            raise ValueError(
                f"label_retry_delay_seconds 必须为非负整数，收到 "
                f"{self.label_retry_delay_seconds!r}"
            )
        if tuple(self.label_windows) != WINDOWS:
            raise ValueError(
                f"label_windows 固定为 {WINDOWS}，收到 {tuple(self.label_windows)}"
            )

    @classmethod
    def from_settings(cls, settings_obj: Any = None) -> "ShadowCore":
        """从 Settings 构造；字段存在就原样进入 __post_init__ 强校验。

        主审整改 7：不得用 ``value or default`` 吞掉 0/空非法配置——
        ``M3_SHADOW_MAX_BARS=0``、``M3_SHADOW_TIMEFRAME=""`` 必须抛 ValueError，
        不允许静默替换为默认值后绕过语义校验。
        """
        cfg = settings_obj if settings_obj is not None else settings
        symbols = frozenset(
            s.strip().upper() for s in (cfg.M3_SHADOW_SYMBOLS or "").split(",") if s.strip()
        )
        # 直接取字段值（Settings 有默认值；0/空串/None 原样进入 __post_init__ 校验）
        max_bars = cfg.M3_SHADOW_MAX_BARS
        if max_bars is None:
            max_bars = DEFAULT_MAX_BARS
        timeframe = cfg.M3_SHADOW_TIMEFRAME
        if timeframe is None:
            timeframe = FIXED_TIMEFRAME
        return cls(
            enabled=bool(cfg.M3_SHADOW_ENABLED),
            symbols=symbols,
            timeframe=str(timeframe),
            max_bars=int(max_bars),
            retry_delay_seconds=int(cfg.M3_SHADOW_RETRY_DELAY_SECONDS),
            max_attempts=int(cfg.M3_SHADOW_MAX_ATTEMPTS),
            ingest_limit=int(cfg.M3_SHADOW_INGEST_LIMIT),
            label_limit=int(cfg.M3_SHADOW_LABEL_LIMIT),
            label_retry_delay_seconds=int(cfg.M3_SHADOW_LABEL_RETRY_DELAY_SECONDS),
        )

    def window_boundary(self, signal_time: int, w: int) -> int:
        """窗口 w 的成熟边界（= signal_time + (w+1)*step，M3A 闭合公式）。"""
        if w not in WINDOW_BOUNDARY_OFFSETS:
            raise ValueError(f"非法标签窗口: {w!r}（支持 {sorted(WINDOW_BOUNDARY_OFFSETS)}）")
        return signal_time + WINDOW_BOUNDARY_OFFSETS[w]

    def next_label_boundary(self, rec: "ShadowRecord", now: int) -> tuple[int, int] | None:
        """返回 (label_as_of, 窗口 w) 若标签更新到期，否则 None。

        - pending（含首次）/ failed 且无 next_label_at → 从 5 窗口起算；
        - labeled / failed 有 next_label_at → 从该窗口起算；
        - 若 now 已跳过多边界，一次推进到已到期最大窗口（≤ 40）；
        - completed / rejected 不再更新。
        """
        if rec.status in (STATUS_COMPLETED, STATUS_REJECTED):
            return None
        if rec.status == STATUS_FAILED and (
            rec.next_retry_at is None or now < rec.next_retry_at
        ):
            return None
        if rec.status == STATUS_LABELED and rec.next_label_at is not None:
            next_w = next(
                (w for w in self.label_windows
                 if self.window_boundary(rec.signal_time, w) == rec.next_label_at),
                None,
            )
        else:
            next_w = 5
        if next_w is None:
            return None
        boundary = self.window_boundary(rec.signal_time, next_w)
        if now < boundary:
            return None
        target = min(now, self.window_boundary(rec.signal_time, 40))
        w = max(
            (x for x in self.label_windows
             if self.window_boundary(rec.signal_time, x) <= target),
            default=next_w,
        )
        return self.window_boundary(rec.signal_time, w), w


# ═══════════════════════════════════════════════════════════════════════════════
#  数据对象
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class SourceEvent:
    """V1 alert_events 行的只读快照（从 ORM 行或 dict 构造）。"""

    dedupe_key: str
    id: int
    symbol: str
    timeframe: str
    strategy: str
    event_type: str
    direction: str
    severity: str
    signal_time: int
    reference_price: float
    payload: dict
    created_at_epoch: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "dedupe_key": self.dedupe_key,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "strategy": self.strategy,
            "event_type": self.event_type,
            "direction": self.direction,
            "severity": self.severity,
            "signal_time": self.signal_time,
            "reference_price": self.reference_price,
            "payload": self.payload,
            "created_at": self.created_at_epoch,
        }


def source_from_snapshot(snapshot_json: str) -> SourceEvent:
    """从 m3_shadow_events.source_snapshot（canonical JSON）重建 SourceEvent，
    用于 decision 失败行到期重试（不依赖 V1 行仍在）。"""
    data = json.loads(snapshot_json)
    payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
    return SourceEvent(
        dedupe_key=str(data["dedupe_key"]),
        id=int(data["id"]),
        symbol=str(data["symbol"]),
        timeframe=str(data["timeframe"]),
        strategy=str(data.get("strategy") or ""),
        event_type=str(data.get("event_type") or ""),
        direction=str(data.get("direction") or ""),
        severity=str(data.get("severity") or ""),
        signal_time=int(data["signal_time"]),
        reference_price=float(data["reference_price"]),
        payload=payload,
        created_at_epoch=int(data.get("created_at") or 0),
    )


@dataclass(frozen=True)
class Heartbeat:
    """m3_shadow_runtime 单例心跳。"""

    started_at: int
    last_run_at: int | None = None
    last_run_ok: bool | None = None
    last_error: str | None = None


@dataclass
class ShadowRecord:
    """m3_shadow_events 行（可变工作副本）。

    主审整改：decision 失败态（failure_stage=decision）允许 decision 字段为空；
    其余状态 decision 字段必须完整（DB CHECK 守护）。
    """

    source_dedupe_key: str
    source_event_id: int
    symbol: str
    timeframe: str
    signal_time: int
    source_snapshot: str
    decision_json: str | None = None
    decision_digest: str | None = None
    eligible: bool | None = None
    reasons: str | None = None
    config_hash: str | None = None
    status: str = STATUS_PENDING
    failure_stage: str | None = None
    attempts: int = 0
    next_retry_at: int | None = None
    last_error: str | None = None
    label_json: str | None = None
    label_as_of: int | None = None
    maturity_window: int | None = None
    next_label_at: int | None = None


@dataclass(frozen=True)
class ShadowCounts:
    total: int = 0
    eligible: int = 0
    rejected: int = 0
    pending: int = 0
    labeled: int = 0
    completed: int = 0
    failed: int = 0
    decision_failed: int = 0
    label_failed: int = 0
    unique_eligible_digests: int = 0


DAYS_30 = 30.0
TARGET_UNIQUE = 100


def compute_progress(started_at: int, counts: ShadowCounts, now: int) -> dict[str, Any]:
    """30 天与 100 唯一 eligible 进度（纯函数，CLI status 与测试共用）。

    本轮只如实报告进度，不伪造门槛达成（milestone_reached 需两者同时满足）。
    """
    days_elapsed = max(0.0, (now - started_at) / 86400.0)
    return {
        "days_elapsed": round(days_elapsed, 3),
        "days_remaining_30": round(max(0.0, DAYS_30 - days_elapsed), 3),
        "unique_eligible_digests": counts.unique_eligible_digests,
        "unique_eligible_remaining": max(0, TARGET_UNIQUE - counts.unique_eligible_digests),
        "milestone_reached": bool(
            days_elapsed >= DAYS_30 and counts.unique_eligible_digests >= TARGET_UNIQUE
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  存储接口（内存实现供测试；SqlShadowRepo 供生产）
# ═══════════════════════════════════════════════════════════════════════════════

MarketLoader = Callable[[str, str, int, int], Awaitable[list[dict]]]
# (symbol, timeframe, end_ts, max_bars) -> bars（原始，运行时按 close_time 过滤截断）


class ShadowRepo(Protocol):
    """影子运行时存储接口（全部 async；测试用内存实现注入）。"""

    async def ensure_heartbeat(self, now: int) -> Heartbeat: ...
    async def get_heartbeat(self) -> Heartbeat | None: ...
    async def touch_heartbeat(self, now: int, ok: bool, error: str | None) -> None: ...
    async def fetch_candidates(
        self, started_at: int, timeframe: str, symbols: frozenset[str], limit: int
    ) -> list[SourceEvent]: ...
    async def insert_event(self, rec: ShadowRecord) -> bool: ...
    async def insert_failed(
        self, source: SourceEvent, *, error: str, next_retry_at: int, attempts: int,
    ) -> bool: ...
    async def promote_decision(self, key: str, rec: ShadowRecord) -> None: ...
    async def list_decision_retry_due(self, now: int, limit: int) -> list[ShadowRecord]: ...
    async def list_label_due(self, now: int, limit: int) -> list[ShadowRecord]: ...
    async def update_label(
        self, key: str, *, status: str, label_json: str, label_as_of: int,
        maturity_window: int | None, next_label_at: int | None, last_error: str | None,
        next_retry_at: int | None = None,
    ) -> None: ...
    async def mark_failed(
        self, key: str, error: str, next_retry_at: int | None, stage: str,
    ) -> None: ...
    async def counts(self) -> ShadowCounts: ...
    async def get_by_key(self, key: str) -> ShadowRecord | None: ...


# ── 行内容严格验证（读回不信任脏行；主审整改 7）──────────────────────────────

def validate_row_content(rec: ShadowRecord) -> None:
    """读回行内容严格验证（不信任脏行；与 DB CHECK / ShadowEventRow 三层统一）。

    - decision 非空 → validate_decision（M2 严格 schema）+ canonical 字符串 +
      digest 重算一致 + eligible/config_hash/reasons 一致；
    - label 非空 → validate_outcome_label（M3A 严格 schema）+ canonical 字符串 +
      label.decision_digest == 行 digest、label_as_of 一致、实际 matured 最大值
      == 行 maturity_window（交换另一条合法 label / 伪造字符串 → 拒绝）；
    - 状态机一致性：
      failed/decision → decision 五字段全 NULL 且 label 空；
      rejected → eligible=false 且无 label；
      pending/labeled/completed/failed-label → eligible=true；
      labeled → 真实 maturity<40；completed → maturity=40；
      非 failed → attempts=0、last_error 空（pending/labeled 允许缺数据
      next_retry_at backoff；rejected/completed 不允许）；
    - 任何不一致/伪造 → ValueError（脏行拒绝，由调用方 fail-safe）。
    """
    if rec.decision_json is not None:
        try:
            d = json.loads(rec.decision_json)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"decision_json 非法 JSON（脏行）: {exc}") from exc
        if canonical_json(d) != rec.decision_json:
            raise ValueError("decision_json 非 canonical JSON（脏行）")
        validated = validate_decision(d)
        if rec.decision_digest != decision_digest(d):
            raise ValueError(
                f"decision_digest 与 decision_json 不一致（脏行）: "
                f"{rec.decision_digest} != {decision_digest(d)}"
            )
        if rec.eligible != bool(validated.eligible):
            raise ValueError("eligible 与 decision_json 不一致（脏行）")
        if rec.config_hash != canonical_json(d["config_hash"]):
            raise ValueError("config_hash 与 decision_json 不一致（脏行）")
        if rec.reasons is None:
            raise ValueError("reasons 为空但 decision 非空（脏行）")
        expected_reasons = canonical_json({
            "codes": d["reason_codes"],
            "explanations": d["explanations"],
        })
        # reasons canonical 契约：必须与期望字符串**逐字节一致**；
        # 不能把输入重新 canonicalize 后比较（否则同内容但 key 顺序/空白/
        # 引号不同的非规范序列化会被放行）。非法 JSON 也必然 != canonical。
        if rec.reasons != expected_reasons:
            raise ValueError(
                "reasons 非 canonical 字符串（与 decision reason_codes/"
                "explanations 不一致）（脏行）"
            )
    else:
        if rec.status != STATUS_FAILED or rec.failure_stage != STAGE_DECISION:
            raise ValueError(
                "decision 字段为空但状态不是 failed/decision（脏行）"
            )
    if rec.label_json is not None:
        try:
            lab = json.loads(rec.label_json)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"label_json 非法 JSON（脏行）: {exc}") from exc
        validate_outcome_label(lab)
        if canonical_json(lab) != rec.label_json:
            raise ValueError("label_json 非 canonical JSON（脏行）")
        if lab.get("decision_digest") != rec.decision_digest:
            raise ValueError("label.decision_digest 与行 decision_digest 不一致（脏行）")
        if lab.get("label_as_of") != rec.label_as_of:
            raise ValueError(
                f"label.label_as_of({lab.get('label_as_of')}) 与行 label_as_of("
                f"{rec.label_as_of}) 不一致（脏行）"
            )
        matured = [
            w for w in lab.get("windows", [])
            if isinstance(w, dict) and w.get("matured") and w.get("window") in WINDOWS
        ]
        actual_mw = max((w["window"] for w in matured), default=None)
        if rec.maturity_window != actual_mw:
            raise ValueError(
                f"maturity_window({rec.maturity_window}) != 实际成熟最大值("
                f"{actual_mw})（脏行）"
            )
    _validate_state_machine(rec)


def _validate_state_machine(rec: ShadowRecord) -> None:
    """状态机一致性（与 DB CHECK / ShadowEventRow 三层统一；关键非法行逐一拒绝）。"""
    s = rec.status
    if s == STATUS_FAILED:
        # failed 三层契约：attempts>=1、last_error 非空白（btrim）、next_retry_at 非空
        if rec.attempts < 1:
            raise ValueError("failed 必须 attempts>=1（脏行）")
        if rec.next_retry_at is None:
            raise ValueError("failed 必须 next_retry_at 非空（脏行）")
        if not rec.last_error or not rec.last_error.strip():
            raise ValueError("failed 必须 last_error 非空白（脏行）")
        if rec.failure_stage == STAGE_DECISION:
            if any(f is not None for f in (
                rec.decision_json, rec.decision_digest, rec.eligible,
                rec.reasons, rec.config_hash,
            )):
                raise ValueError("failed/decision 必须 decision 五字段全 NULL（脏行）")
            if rec.label_json is not None or rec.label_as_of is not None \
                    or rec.maturity_window is not None:
                raise ValueError("failed/decision 不得携带 label（脏行）")
        elif rec.failure_stage == STAGE_LABEL:
            if rec.eligible is not True:
                raise ValueError("failed/label 必须 eligible=true（脏行）")
            if rec.decision_json is None or rec.decision_digest is None \
                    or rec.reasons is None or rec.config_hash is None:
                raise ValueError("failed/label 不得丢 decision（脏行）")
        else:
            raise ValueError("failed 必须带 failure_stage∈{decision,label}（脏行）")
    elif s == STATUS_REJECTED:
        if rec.eligible is not False:
            raise ValueError("rejected 必须 eligible=false（脏行）")
        if rec.label_json is not None or rec.label_as_of is not None \
                or rec.maturity_window is not None:
            raise ValueError("rejected 不得携带 label（脏行）")
        if rec.attempts != 0 or rec.next_retry_at is not None or rec.last_error is not None:
            raise ValueError("rejected 必须 attempts=0 且 next_retry/error 为空（脏行）")
    elif s in (STATUS_PENDING, STATUS_LABELED, STATUS_COMPLETED):
        if rec.eligible is not True:
            raise ValueError(f"{s} 必须 eligible=true（脏行）")
        if rec.attempts != 0 or rec.last_error is not None:
            raise ValueError(f"非 failed（{s}）必须 attempts=0 且 error 为空（脏行）")
        if s == STATUS_PENDING:
            if rec.label_json is not None:
                # pending 只允许 censored 标签（label_as_of 有、maturity NULL）
                if rec.label_as_of is None or rec.maturity_window is not None:
                    raise ValueError("pending 标签必须 censored（label_as_of 有、maturity 空）")
            elif rec.label_as_of is not None or rec.maturity_window is not None:
                raise ValueError("pending 无标签时 label_as_of/maturity 必须为空（脏行）")
        else:
            if rec.label_json is None or rec.label_as_of is None \
                    or rec.maturity_window is None:
                raise ValueError(f"{s} 必须有真实 label/maturity（脏行）")
            if s == STATUS_LABELED and rec.maturity_window >= 40:
                raise ValueError("labeled 必须 maturity<40（脏行）")
            if s == STATUS_COMPLETED:
                if rec.maturity_window != 40:
                    raise ValueError("completed 必须 maturity=40（脏行）")
                if rec.next_retry_at is not None:
                    raise ValueError("completed 不得有 next_retry_at（脏行）")
    else:
        raise ValueError(f"非法 status: {s!r}（脏行）")


class MemoryShadowRepo:
    """内存实现：模拟唯一键冲突、心跳单例、状态过滤（供测试注入）。"""

    def __init__(self, *, core: ShadowCore | None = None) -> None:
        self._lock = asyncio.Lock()
        self._core = core or ShadowCore()
        self._heartbeat: Heartbeat | None = None
        self._events: dict[str, ShadowRecord] = {}
        self._source_rows: list[SourceEvent] = []

    # ── 测试辅助 ──────────────────────────────────────────────────────────
    def seed_source(self, rows: list[SourceEvent]) -> None:
        self._source_rows = list(rows)

    # ── ShadowRepo ────────────────────────────────────────────────────────
    async def ensure_heartbeat(self, now: int) -> Heartbeat:
        async with self._lock:
            if self._heartbeat is None:
                self._heartbeat = Heartbeat(started_at=now)
            return self._heartbeat

    async def get_heartbeat(self) -> Heartbeat | None:
        return self._heartbeat

    async def touch_heartbeat(self, now: int, ok: bool, error: str | None) -> None:
        hb = self._heartbeat or Heartbeat(started_at=now)
        self._heartbeat = Heartbeat(
            started_at=hb.started_at, last_run_at=now,
            last_run_ok=ok, last_error=error,
        )

    async def fetch_candidates(
        self, started_at: int, timeframe: str, symbols: frozenset[str], limit: int
    ) -> list[SourceEvent]:
        """与 SQL 同口径：LIMIT 前排除已处理源（含 failed 行）→ 分页不饥饿。"""
        out = []
        for row in self._source_rows:
            if row.dedupe_key in self._events:
                continue  # 已处理（含 failed(decision) 行）
            if row.created_at_epoch < started_at:
                continue
            if row.timeframe != timeframe:
                continue
            if symbols and row.symbol.upper() not in symbols:
                continue
            out.append(row)
            if len(out) >= limit:
                break
        return out

    async def insert_event(self, rec: ShadowRecord) -> bool:
        if rec.source_dedupe_key in self._events:
            return False  # 模拟 DB 唯一键冲突：同源重复不产生第二行
        self._events[rec.source_dedupe_key] = rec
        return True

    async def insert_failed(
        self, source: SourceEvent, *, error: str, next_retry_at: int, attempts: int,
    ) -> bool:
        """decision 失败行落库（唯一 key；冲突忽略）。"""
        rec = ShadowRecord(
            source_dedupe_key=source.dedupe_key,
            source_event_id=source.id,
            symbol=source.symbol,
            timeframe=source.timeframe,
            signal_time=source.signal_time,
            source_snapshot=canonical_json(source.to_dict()),
            status=STATUS_FAILED,
            failure_stage=STAGE_DECISION,
            attempts=attempts,
            next_retry_at=next_retry_at,
            last_error=error,
        )
        return await self.insert_event(rec)

    async def promote_decision(self, key: str, rec: ShadowRecord) -> None:
        """decision 失败行到期重试成功后：原行升级补全（不产生重复行）。"""
        current = self._events[key]
        current.decision_json = rec.decision_json
        current.decision_digest = rec.decision_digest
        current.eligible = rec.eligible
        current.reasons = rec.reasons
        current.config_hash = rec.config_hash
        current.status = rec.status  # pending / rejected
        current.failure_stage = None
        current.attempts = 0
        current.next_retry_at = None
        current.last_error = None

    async def list_decision_retry_due(self, now: int, limit: int) -> list[ShadowRecord]:
        core = self._core
        out = []
        for rec in sorted(self._events.values(), key=lambda r: r.signal_time):
            if rec.status != STATUS_FAILED or rec.failure_stage != STAGE_DECISION:
                continue
            if rec.attempts >= core.max_attempts:
                continue
            if rec.next_retry_at is not None and now < rec.next_retry_at:
                continue
            out.append(rec)
            if len(out) >= limit:
                break
        return out

    async def list_label_due(self, now: int, limit: int) -> list[ShadowRecord]:
        core = self._core
        out = []
        # 公平轮转（与 Sql 同口径）：新鲜（无 backoff）优先，其余按 backoff
        # 最早到期优先 → 被处理者跳到队尾，同批重试不会永远压住新鲜后排
        def _key(r: ShadowRecord):
            return (
                r.next_retry_at is not None,          # False=新鲜在前
                r.next_retry_at if r.next_retry_at is not None else 0,
                r.signal_time,
            )
        for rec in sorted(self._events.values(), key=_key):
            if rec.status in (STATUS_COMPLETED, STATUS_REJECTED):
                continue
            if rec.status == STATUS_FAILED:
                if rec.failure_stage == STAGE_DECISION:
                    continue  # decision 失败走 list_decision_retry_due
                if rec.attempts >= core.max_attempts:
                    continue
            # 全部状态尊重 backoff（failed 的 label 重试 / pending+labeled 的
            # 缺数据 backoff），到期前不选（防热循环）
            if rec.next_retry_at is not None and now < rec.next_retry_at:
                continue
            if core.next_label_boundary(rec, now) is None:
                continue
            out.append(rec)
            if len(out) >= limit:
                break
        return out

    async def update_label(
        self, key: str, *, status: str, label_json: str, label_as_of: int,
        maturity_window: int | None, next_label_at: int | None, last_error: str | None,
        next_retry_at: int | None = None,
    ) -> None:
        rec = self._events[key]
        rec.status = status
        rec.label_json = label_json
        rec.label_as_of = label_as_of
        rec.maturity_window = maturity_window
        rec.next_label_at = next_label_at
        rec.last_error = last_error
        rec.next_retry_at = next_retry_at  # 缺数据 backoff（成功/清空传 None）
        rec.attempts = 0
        rec.failure_stage = None  # 成功推进清除 failed/label 阶段（与 Sql 一致）

    async def mark_failed(
        self, key: str, error: str, next_retry_at: int | None, stage: str,
    ) -> None:
        rec = self._events[key]
        # failed 三层契约防御：空/全空白错误文本违反约束 → 安全兜底
        if not error or not error.strip():
            error = "unknown m3 shadow failure"
        rec.status = STATUS_FAILED
        rec.failure_stage = stage
        rec.attempts += 1
        rec.next_retry_at = next_retry_at
        rec.last_error = error

    async def counts(self) -> ShadowCounts:
        total = len(self._events)
        eligible = sum(1 for r in self._events.values() if r.eligible)
        return ShadowCounts(
            total=total,
            eligible=eligible,
            rejected=sum(1 for r in self._events.values() if r.status == STATUS_REJECTED),
            pending=sum(1 for r in self._events.values() if r.status == STATUS_PENDING),
            labeled=sum(1 for r in self._events.values() if r.status == STATUS_LABELED),
            completed=sum(1 for r in self._events.values() if r.status == STATUS_COMPLETED),
            failed=sum(1 for r in self._events.values() if r.status == STATUS_FAILED),
            decision_failed=sum(
                1 for r in self._events.values()
                if r.status == STATUS_FAILED and r.failure_stage == STAGE_DECISION
            ),
            label_failed=sum(
                1 for r in self._events.values()
                if r.status == STATUS_FAILED and r.failure_stage == STAGE_LABEL
            ),
            unique_eligible_digests=len({
                r.decision_digest for r in self._events.values() if r.eligible
            }),
        )

    async def get_by_key(self, key: str) -> ShadowRecord | None:
        rec = self._events.get(key)
        if rec is None:
            return None
        validate_row_content(rec)  # 读回严格验证（不信任脏行）
        return rec


# ═══════════════════════════════════════════════════════════════════════════════
#  纯计算：事件 → 决策记录
# ═══════════════════════════════════════════════════════════════════════════════

def build_trigger_event(source: SourceEvent) -> dict:
    """从 V1 alert_events 行构造 M2 trigger_event 字典（不改动原始 payload）。"""
    payload = dict(source.payload or {})
    payload.setdefault("timeframe", source.timeframe)
    return {
        "strategy": source.strategy,
        "event_type": source.event_type,
        "direction": source.direction,
        "severity": source.severity,
        "signal_time": source.signal_time,
        "reference_price": source.reference_price,
        "summary": str((payload or {}).get("summary") or ""),
        "payload": payload,
    }


def compute_decision_record(
    core: ShadowCore,
    source: SourceEvent,
    candles_by_timeframe: dict[str, list[dict]],
    as_of: int,
) -> ShadowRecord:
    """M2 决策 → 严格校验 → canonical JSON + digest → ShadowRecord。

    读（decision dict）写（decision_json）均过 M2 严格 validator
    （validate_decision）；JSON allow_nan=False。
    """
    trigger = build_trigger_event(source)
    decision = evaluate(
        source.symbol,
        candles_by_timeframe,
        as_of,
        trigger,
        core.market_state_config,
        core.fusion_config,
    )
    d = decision.to_dict()
    validate_decision(d)  # 写库前过既有严格 validator
    decision_json = canonical_json(d)
    digest = decision_digest(d)
    return ShadowRecord(
        source_dedupe_key=source.dedupe_key,
        source_event_id=source.id,
        symbol=source.symbol,
        timeframe=source.timeframe,
        signal_time=source.signal_time,
        source_snapshot=canonical_json(source.to_dict()),
        decision_json=decision_json,
        decision_digest=digest,
        eligible=bool(d["eligible"]),
        reasons=canonical_json({
            "codes": d["reason_codes"],
            "explanations": d["explanations"],
        }),
        config_hash=canonical_json(d["config_hash"]),
        status=STATUS_PENDING if d["eligible"] else STATUS_REJECTED,
    )


def _matured_windows(label_dict: dict[str, Any], windows: tuple[int, ...]) -> list[int]:
    return [
        w for w in windows
        if any(x.get("window") == w and x.get("matured") for x in label_dict.get("windows", []))
    ]


def compute_label_record(
    core: ShadowCore,
    rec: ShadowRecord,
    candles_4h: list[dict],
    label_as_of: int,
) -> tuple[str, str, int | None, int | None]:
    """M3A 标签 → 严格校验 → (label_json, status, maturity_window, next_label_at)。

    写库前过 validate_outcome_label（M3A 严格 validator）。

    成熟真实性（主审整改 5）：maturity_window = 标签中实际成熟窗口最大值；
    没有成熟窗口（数据未到齐/缺根）→ None 且 status 保持 pending（不虚报、
    不跳窗口）；只有 40 真实成熟才 completed；到期窗口缺根/未收盘时
    next_label_at 仍指向该窗口（保持可重试）。
    """
    decision = json.loads(rec.decision_json)  # label 阶段 decision 必须完整
    lab = label_decision(decision, candles_4h, label_as_of, core.market_state_config)
    lab_d = lab.to_dict()
    validate_outcome_label(lab_d)
    label_json = canonical_json(lab_d)
    matured = _matured_windows(lab_d, core.label_windows)
    mw = max(matured) if matured else None
    if mw is None:
        # 数据未到齐：不虚报成熟；status 保持 pending，等待重试
        return label_json, STATUS_PENDING, None, None
    if mw >= 40:
        return label_json, STATUS_COMPLETED, 40, None
    nxt = next((w for w in core.label_windows if w > mw), None)
    next_at = core.window_boundary(rec.signal_time, nxt) if nxt else None
    return label_json, STATUS_LABELED, mw, next_at


# ═══════════════════════════════════════════════════════════════════════════════
#  一轮 cycle（可注入 repo / loader / 时钟）
# ═══════════════════════════════════════════════════════════════════════════════

async def run_cycle(
    core: ShadowCore,
    repo: ShadowRepo,
    loader: MarketLoader,
    now: int,
) -> dict[str, Any]:
    """一轮完整 cycle：ensure 心跳 → 摄取新 4H 事件 → decision 失败重试 →
    标签到期更新 → 心跳。

    幂等/崩溃安全/隔离（主审整改 1/2）：
      - 摄取按 source dedupe key 唯一（冲突忽略），重跑不重复计数；
      - fetch_candidates 在 LIMIT 前排除已处理（分页不饥饿）；
      - 单事件 decision/load 失败也落库（failure_stage=decision，唯一行）并
        退避重试，不阻塞同轮后续好源、不使整轮退出；
      - decision 失败行到期重试成功后原行升级（不产生重复行），达
        max_attempts 停止；
      - 标签只向前推进（maturity 单调不减），completed 不改写；
      - 标签失败（failure_stage=label）不得丢 decision；
      - 同 (symbol, as_of) / (symbol, label_as_of) 复用行情取数。
    """
    hb = await repo.ensure_heartbeat(now)
    ingested = labeled = failed = 0
    decision_failed = 0
    decision_retried = 0
    attempted: set[str] = set()  # 本轮已尝试事件（防同一轮内重复处理）
    cache: dict[tuple[str, int, str], list[dict]] = {}
    load_calls = 0

    async def _load(symbol: str, timeframe: str, end_ts: int) -> list[dict]:
        nonlocal load_calls
        key = (symbol, end_ts, timeframe)
        if key in cache:
            return cache[key]
        bars = await loader(symbol, timeframe, end_ts, core.max_bars)
        load_calls += 1
        cache[key] = bars
        return bars

    async def _compute(source: SourceEvent) -> ShadowRecord:
        """取数（三周期）+ 决策（一个坏事件只影响自己）。"""
        as_of = source.signal_time + core.step_4h
        candles_by_tf = {}
        for tf in ("1week", "1day", core.timeframe):
            candles_by_tf[tf] = await _load(source.symbol, tf, as_of)
        return compute_decision_record(core, source, candles_by_tf, as_of)

    # ── 1. 新事件摄取（decision 失败也落库，不阻塞后续好源）──────────────
    candidates = await repo.fetch_candidates(
        hb.started_at, core.timeframe, core.symbols, core.ingest_limit
    )
    for source in candidates:
        if source.dedupe_key in attempted:
            continue
        attempted.add(source.dedupe_key)
        try:
            rec = await _compute(source)
        except Exception as exc:
            await repo.insert_failed(
                source,
                error=sanitize_error(exc) or "decision failed",
                next_retry_at=now + core.retry_delay_seconds,
                attempts=1,
            )
            decision_failed += 1
            continue
        inserted = await repo.insert_event(rec)
        if not inserted:
            continue  # 并发/重跑冲突：另一轮已插入
        ingested += 1
        if rec.eligible:
            boundary = core.next_label_boundary(rec, now)
            if boundary is not None:
                label_as_of, _w = boundary
                try:
                    bars4h = await _load(rec.symbol, core.timeframe, label_as_of)
                    await _apply_label(core, repo, rec, bars4h, label_as_of, now)
                    labeled += 1
                except Exception as exc:
                    await repo.mark_failed(
                        rec.source_dedupe_key,
                        sanitize_error(exc) or "label failed",
                        now + core.retry_delay_seconds,
                        stage=STAGE_LABEL,
                    )
                    failed += 1

    # ── 2. decision 失败行到期重试（原行升级，不产生重复行）──────────────
    due_dec = await repo.list_decision_retry_due(now, core.ingest_limit)
    for rec in due_dec:
        if rec.source_dedupe_key in attempted:
            continue
        attempted.add(rec.source_dedupe_key)
        try:
            source = source_from_snapshot(rec.source_snapshot)
            new_rec = await _compute(source)
        except Exception as exc:
            await repo.mark_failed(
                rec.source_dedupe_key,
                sanitize_error(exc) or "decision failed",
                now + core.retry_delay_seconds,
                stage=STAGE_DECISION,
            )
            decision_failed += 1
            continue
        await repo.promote_decision(rec.source_dedupe_key, new_rec)
        decision_retried += 1
        if new_rec.eligible:
            boundary = core.next_label_boundary(new_rec, now)
            if boundary is not None:
                label_as_of, _w = boundary
                try:
                    bars4h = await _load(new_rec.symbol, core.timeframe, label_as_of)
                    await _apply_label(core, repo, new_rec, bars4h, label_as_of, now)
                    labeled += 1
                except Exception as exc:
                    await repo.mark_failed(
                        new_rec.source_dedupe_key,
                        sanitize_error(exc) or "label failed",
                        now + core.retry_delay_seconds,
                        stage=STAGE_LABEL,
                    )
                    failed += 1

    # ── 3. 标签到期更新（pending/labeled/failed(label) 可重试）────────────
    due = await repo.list_label_due(now, core.label_limit)
    for rec in due:
        if rec.source_dedupe_key in attempted:
            continue
        boundary = core.next_label_boundary(rec, now)
        if boundary is None:
            continue
        label_as_of, _w = boundary
        attempted.add(rec.source_dedupe_key)
        try:
            bars4h = await _load(rec.symbol, core.timeframe, label_as_of)
            await _apply_label(core, repo, rec, bars4h, label_as_of, now)
            labeled += 1
        except Exception as exc:
            await repo.mark_failed(
                rec.source_dedupe_key,
                sanitize_error(exc) or "label failed",
                now + core.retry_delay_seconds,
                stage=STAGE_LABEL,
            )
            failed += 1

    # ── 4. 心跳 ────────────────────────────────────────────────────────────
    await repo.touch_heartbeat(now, ok=True, error=None)
    return {
        "ingested": ingested,
        "labeled": labeled,
        "failed": failed,
        "decision_failed": decision_failed,
        "decision_retried": decision_retried,
        "load_calls": load_calls,
        "candidates_seen": len(candidates),
    }


async def _apply_label(
    core: ShadowCore,
    repo: ShadowRepo,
    rec: ShadowRecord,
    candles_4h: list[dict],
    label_as_of: int,
    now: int,
) -> None:
    """计算标签并写入；缺数据/部分成熟（未到目标窗）设 next_retry_at backoff
    防热循环（不算 failed，保持可重试），成功推进后清空。"""
    label_json, status, mw, next_at = compute_label_record(
        core, rec, candles_4h, label_as_of,
    )
    retry_at = None
    if status == STATUS_PENDING:
        # 0 成熟（缺根/未收盘/边界未到）：censored 标签已写入，backoff 防热循环
        retry_at = now + core.label_retry_delay_seconds
    elif status == STATUS_LABELED and next_at is not None and next_at == label_as_of:
        # 部分成熟：目标窗（next_at）未成熟 → 同窗重试，backoff 防热循环
        retry_at = now + core.label_retry_delay_seconds
    await repo.update_label(
        rec.source_dedupe_key,
        status=status,
        label_json=label_json,
        label_as_of=label_as_of,
        maturity_window=mw,
        next_label_at=next_at,
        last_error=None,
        next_retry_at=retry_at,
    )


async def run_cycle_safe(
    core: ShadowCore,
    repo: ShadowRepo,
    loader: MarketLoader,
    now: int,
) -> dict[str, Any]:
    """V1 隔离包装：内部异常只写 heartbeat.last_error，永不抛出。"""
    try:
        return await run_cycle(core, repo, loader, now)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        error = sanitize_error(exc) or "unknown error"
        logger.exception("m3 shadow cycle failed: %s", error)
        try:
            await repo.touch_heartbeat(now, ok=False, error=error)
        except Exception:
            logger.exception("m3 shadow heartbeat error write failed")
        return {"ok": False, "error": error}


# ═══════════════════════════════════════════════════════════════════════════════
#  生产实现（SQLAlchemy async + 行情源只读）
# ═══════════════════════════════════════════════════════════════════════════════

def _datetime_to_epoch(value: Any) -> int:
    """naive UTC DateTime → epoch 秒（与项目 utcnow 一致）。"""
    if value is None:
        return 0
    if hasattr(value, "timestamp"):
        return int(value.timestamp())
    return int(value)


def fetch_candidates_stmt(
    started_at: int, timeframe: str, symbols: frozenset[str], limit: int,
):
    """候选查询（可测 SQL 结构）：**在 LIMIT 之前**用 NOT EXISTS 排除已处理源
    （含 failed 行），解决分页饥饿；只读 V1 alert_events，不写 V1。"""
    handled = exists(
        select(1).where(M3ShadowEvent.source_dedupe_key == AlertEvent.dedupe_key)
    )
    stmt = (
        select(AlertEvent)
        .where(
            AlertEvent.timeframe == timeframe,
            func.extract("epoch", AlertEvent.created_at) >= started_at,
            ~handled,
        )
        .order_by(AlertEvent.id)
        .limit(limit)
    )
    if symbols:
        stmt = stmt.where(AlertEvent.symbol.in_(sorted(symbols)))
    return stmt


def _rec_from_row(row: Any) -> ShadowRecord:
    rec = ShadowRecord(
        source_dedupe_key=row.source_dedupe_key,
        source_event_id=int(row.source_event_id),
        symbol=row.symbol,
        timeframe=row.timeframe,
        signal_time=int(row.signal_time),
        source_snapshot=row.source_snapshot,
        decision_json=row.decision_json,
        decision_digest=row.decision_digest,
        eligible=bool(row.eligible) if row.eligible is not None else None,
        reasons=row.reasons,
        config_hash=row.config_hash,
        status=row.status,
        failure_stage=row.failure_stage,
        attempts=row.attempts,
        next_retry_at=int(row.next_retry_at) if row.next_retry_at is not None else None,
        last_error=row.last_error,
        label_json=row.label_json,
        label_as_of=int(row.label_as_of) if row.label_as_of is not None else None,
        maturity_window=row.maturity_window,
        next_label_at=int(row.next_label_at) if row.next_label_at is not None else None,
    )
    validate_row_content(rec)  # 读回严格验证（不信任脏行）
    return rec


def list_decision_retry_due_stmt(now: int, limit: int, max_attempts: int):
    """decision 失败行到期重试查询（可测 SQL 结构；max_attempts 来自 core）。"""
    from sqlalchemy import or_

    return (
        select(M3ShadowEvent)
        .where(
            M3ShadowEvent.status == STATUS_FAILED,
            M3ShadowEvent.failure_stage == STAGE_DECISION,
            M3ShadowEvent.attempts < max_attempts,
            or_(
                M3ShadowEvent.next_retry_at.is_(None),
                M3ShadowEvent.next_retry_at <= now,
            ),
        )
        .order_by(M3ShadowEvent.signal_time)
        .limit(limit)
    )


def list_label_due_stmt(now: int, limit: int, max_attempts: int):
    """标签到期更新查询（可测 SQL 结构）。

    - pending：5 边界已到（且 next_retry_at backoff 到期）；
    - labeled：next_label_at 已到（且 backoff 到期）；
    - failed/label：backoff 到期 +（next_label_at 已到，或 next_label_at NULL →
      按 W5 边界重试）——首次 W5 标签异常行不会永远不被选中；
    - 全部状态尊重 next_retry_at backoff（缺数据防热循环）；
    - 排序：新鲜（无 backoff）优先，其余按 backoff 最早到期优先（+signal_time
      稳定次序）→ 被处理者跳到队尾，同批重试不会永远压住新鲜后排。
    """
    from sqlalchemy import and_, or_

    return (
        select(M3ShadowEvent)
        .where(
            M3ShadowEvent.status.in_((STATUS_PENDING, STATUS_LABELED, STATUS_FAILED)),
            M3ShadowEvent.eligible.is_(True),
            or_(
                M3ShadowEvent.status != STATUS_FAILED,
                and_(
                    M3ShadowEvent.failure_stage == STAGE_LABEL,
                    M3ShadowEvent.attempts < max_attempts,
                ),
            ),
            or_(
                M3ShadowEvent.next_retry_at.is_(None),
                M3ShadowEvent.next_retry_at <= now,
            ),
            or_(
                and_(
                    M3ShadowEvent.status == STATUS_PENDING,
                    now >= M3ShadowEvent.signal_time + WINDOW_BOUNDARY_OFFSETS[5],
                ),
                and_(
                    M3ShadowEvent.status == STATUS_LABELED,
                    M3ShadowEvent.next_label_at.is_not(None),
                    now >= M3ShadowEvent.next_label_at,
                ),
                and_(
                    M3ShadowEvent.status == STATUS_FAILED,
                    or_(
                        and_(
                            M3ShadowEvent.next_label_at.is_not(None),
                            now >= M3ShadowEvent.next_label_at,
                        ),
                        and_(
                            M3ShadowEvent.next_label_at.is_(None),
                            now >= M3ShadowEvent.signal_time + WINDOW_BOUNDARY_OFFSETS[5],
                        ),
                    ),
                ),
            ),
        )
        .order_by(
            M3ShadowEvent.next_retry_at.is_(None).desc(),  # 新鲜优先
            M3ShadowEvent.next_retry_at.asc(),            # 最早到期优先
            M3ShadowEvent.signal_time,
        )
        .limit(limit)
    )


class SqlShadowRepo:
    """生产 ShadowRepo：SQLAlchemy async session 实现。

    - ensure_heartbeat 用 INSERT ... ON CONFLICT (id) DO NOTHING 原子创建
      （并发下 started_at 只首次设置）；不建 V1 外键；
    - insert_event / insert_failed 冲突（source_dedupe_key 唯一）→ 忽略；
    - fetch_candidates 用 NOT EXISTS 在 LIMIT 前排除已处理（分页不饥饿）；
    - max_attempts 取自构造传入的 core（不硬编码）；
    - 读回（get_by_key / list_*）重新验证 decision/label 内容与 digest。
    """

    def __init__(self, session, core: ShadowCore | None = None) -> None:
        self._session = session
        self._core = core or ShadowCore()

    def _max_attempts(self) -> int:
        return self._core.max_attempts

    async def ensure_heartbeat(self, now: int) -> Heartbeat:
        stmt = pg_insert(M3ShadowRuntime).values(
            id=HEARTBEAT_ID, started_at=now,
        ).on_conflict_do_nothing(index_elements=["id"])
        await self._session.execute(stmt)
        row = await self._session.execute(
            select(M3ShadowRuntime).where(M3ShadowRuntime.id == HEARTBEAT_ID)
        )
        hb = row.scalar_one_or_none()
        if hb is None:
            raise RuntimeError("m3_shadow_runtime 心跳行不存在（表未 apply？）")
        return Heartbeat(
            started_at=int(hb.started_at),
            last_run_at=_datetime_to_epoch(hb.last_run_at) if hb.last_run_at is not None else None,
            last_run_ok=hb.last_run_ok,
            last_error=hb.last_error,
        )

    async def get_heartbeat(self) -> Heartbeat | None:
        row = await self._session.execute(
            select(M3ShadowRuntime).where(M3ShadowRuntime.id == HEARTBEAT_ID)
        )
        hb = row.scalar_one_or_none()
        if hb is None:
            return None
        return Heartbeat(
            started_at=int(hb.started_at),
            last_run_at=_datetime_to_epoch(hb.last_run_at) if hb.last_run_at is not None else None,
            last_run_ok=hb.last_run_ok,
            last_error=hb.last_error,
        )

    async def touch_heartbeat(self, now: int, ok: bool, error: str | None) -> None:
        await self._session.execute(
            update(M3ShadowRuntime)
            .where(M3ShadowRuntime.id == HEARTBEAT_ID)
            .values(last_run_at=now, last_run_ok=ok, last_error=error)
        )

    async def fetch_candidates(
        self, started_at: int, timeframe: str, symbols: frozenset[str], limit: int
    ) -> list[SourceEvent]:
        stmt = fetch_candidates_stmt(started_at, timeframe, symbols, limit)
        result = await self._session.execute(stmt)
        out: list[SourceEvent] = []
        for row in result.scalars().all():
            try:
                payload = json.loads(row.payload or "{}")
            except (TypeError, ValueError):
                payload = {}
            out.append(SourceEvent(
                dedupe_key=row.dedupe_key,
                id=int(row.id),
                symbol=row.symbol,
                timeframe=row.timeframe,
                strategy=row.strategy,
                event_type=row.event_type,
                direction=row.direction,
                severity=row.severity,
                signal_time=int(row.signal_time),
                reference_price=float(row.reference_price),
                payload=payload if isinstance(payload, dict) else {},
                created_at_epoch=int(row.created_at.timestamp()),
            ))
        return out

    async def insert_event(self, rec: ShadowRecord) -> bool:
        stmt = pg_insert(M3ShadowEvent).values(
            source_dedupe_key=rec.source_dedupe_key,
            source_event_id=rec.source_event_id,
            symbol=rec.symbol,
            timeframe=rec.timeframe,
            signal_time=rec.signal_time,
            source_snapshot=rec.source_snapshot,
            decision_json=rec.decision_json,
            decision_digest=rec.decision_digest,
            eligible=rec.eligible,
            reasons=rec.reasons,
            config_hash=rec.config_hash,
            status=rec.status,
            failure_stage=rec.failure_stage,
            attempts=rec.attempts,
            next_retry_at=rec.next_retry_at,
            last_error=rec.last_error,
            label_json=rec.label_json,
            label_as_of=rec.label_as_of,
            maturity_window=rec.maturity_window,
            next_label_at=rec.next_label_at,
        ).on_conflict_do_nothing(index_elements=["source_dedupe_key"])
        result = await self._session.execute(stmt)
        return (result.rowcount or 0) > 0

    async def insert_failed(
        self, source: SourceEvent, *, error: str, next_retry_at: int, attempts: int,
    ) -> bool:
        rec = ShadowRecord(
            source_dedupe_key=source.dedupe_key,
            source_event_id=source.id,
            symbol=source.symbol,
            timeframe=source.timeframe,
            signal_time=source.signal_time,
            source_snapshot=canonical_json(source.to_dict()),
            status=STATUS_FAILED,
            failure_stage=STAGE_DECISION,
            attempts=attempts,
            next_retry_at=next_retry_at,
            last_error=error,
        )
        return await self.insert_event(rec)

    async def promote_decision(self, key: str, rec: ShadowRecord) -> None:
        await self._session.execute(
            update(M3ShadowEvent)
            .where(M3ShadowEvent.source_dedupe_key == key)
            .values(
                decision_json=rec.decision_json,
                decision_digest=rec.decision_digest,
                eligible=rec.eligible,
                reasons=rec.reasons,
                config_hash=rec.config_hash,
                status=rec.status,
                failure_stage=None,
                attempts=0,
                next_retry_at=None,
                last_error=None,
            )
        )

    async def list_decision_retry_due(self, now: int, limit: int) -> list[ShadowRecord]:
        stmt = list_decision_retry_due_stmt(now, limit, self._max_attempts())
        result = await self._session.execute(stmt)
        return [_rec_from_row(row) for row in result.scalars().all()]

    async def list_label_due(self, now: int, limit: int) -> list[ShadowRecord]:
        stmt = list_label_due_stmt(now, limit, self._max_attempts())
        result = await self._session.execute(stmt)
        return [_rec_from_row(row) for row in result.scalars().all()]

    async def update_label(
        self, key: str, *, status: str, label_json: str, label_as_of: int,
        maturity_window: int | None, next_label_at: int | None, last_error: str | None,
        next_retry_at: int | None = None,
    ) -> None:
        await self._session.execute(
            update(M3ShadowEvent)
            .where(M3ShadowEvent.source_dedupe_key == key)
            .values(
                status=status, label_json=label_json, label_as_of=label_as_of,
                maturity_window=maturity_window, next_label_at=next_label_at,
                last_error=last_error, attempts=0, failure_stage=None,
                next_retry_at=next_retry_at,
            )
        )

    async def mark_failed(
        self, key: str, error: str, next_retry_at: int | None, stage: str,
    ) -> None:
        # failed 三层契约防御：空/全空白错误文本违反 DB CHECK → 安全兜底
        if not error or not error.strip():
            error = "unknown m3 shadow failure"
        await self._session.execute(
            update(M3ShadowEvent)
            .where(M3ShadowEvent.source_dedupe_key == key)
            .values(
                status=STATUS_FAILED,
                failure_stage=stage,
                attempts=M3ShadowEvent.attempts + 1,
                next_retry_at=next_retry_at,
                last_error=error,
            )
        )

    async def counts(self) -> ShadowCounts:
        total = (await self._session.execute(
            select(func.count()).select_from(M3ShadowEvent)
        )).scalar_one()
        eligible = (await self._session.execute(
            select(func.count()).select_from(M3ShadowEvent).where(
                M3ShadowEvent.eligible.is_(True))
        )).scalar_one()
        rejected = (await self._session.execute(
            select(func.count()).select_from(M3ShadowEvent).where(
                M3ShadowEvent.status == STATUS_REJECTED)
        )).scalar_one()
        pending = (await self._session.execute(
            select(func.count()).select_from(M3ShadowEvent).where(
                M3ShadowEvent.status == STATUS_PENDING)
        )).scalar_one()
        labeled = (await self._session.execute(
            select(func.count()).select_from(M3ShadowEvent).where(
                M3ShadowEvent.status == STATUS_LABELED)
        )).scalar_one()
        completed = (await self._session.execute(
            select(func.count()).select_from(M3ShadowEvent).where(
                M3ShadowEvent.status == STATUS_COMPLETED)
        )).scalar_one()
        failed = (await self._session.execute(
            select(func.count()).select_from(M3ShadowEvent).where(
                M3ShadowEvent.status == STATUS_FAILED)
        )).scalar_one()
        decision_failed = (await self._session.execute(
            select(func.count()).select_from(M3ShadowEvent).where(
                M3ShadowEvent.status == STATUS_FAILED,
                M3ShadowEvent.failure_stage == STAGE_DECISION,
            )
        )).scalar_one()
        label_failed = (await self._session.execute(
            select(func.count()).select_from(M3ShadowEvent).where(
                M3ShadowEvent.status == STATUS_FAILED,
                M3ShadowEvent.failure_stage == STAGE_LABEL,
            )
        )).scalar_one()
        unique = (await self._session.execute(
            select(func.count(func.distinct(M3ShadowEvent.decision_digest))).select_from(
                M3ShadowEvent).where(M3ShadowEvent.eligible.is_(True))
        )).scalar_one()
        return ShadowCounts(
            total=int(total), eligible=int(eligible), rejected=int(rejected),
            pending=int(pending), labeled=int(labeled), completed=int(completed),
            failed=int(failed),
            decision_failed=int(decision_failed), label_failed=int(label_failed),
            unique_eligible_digests=int(unique),
        )

    async def get_by_key(self, key: str) -> ShadowRecord | None:
        result = await self._session.execute(
            select(M3ShadowEvent).where(M3ShadowEvent.source_dedupe_key == key)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return _rec_from_row(row)


async def production_market_loader(
    symbol: str, timeframe: str, end_ts: int, max_bars: int,
) -> list[dict]:
    """生产行情 loader（主审整改 4）：把 end_ts **精确**作为 provider 的 before
    传入（before 是开盘时点上界；不是 end_ts+duration，也不是拉最新再后过滤，
    旧事件不会被未来数据挤出历史）；保留 close_time <= end_ts 二次过滤与最后
    max_bars 截断；outputsize 多取 BEFORE_PADDING_BARS 覆盖 provider 边界语义
    差异（未来追加 bar 一律被二次过滤，不改变结果）。
    """
    from app.core.daily_report_sources import DailyReportSources

    duration = TIMEFRAME_DURATIONS.get(timeframe)
    if duration is None:
        raise ValueError(f"非法周期: {timeframe!r}")
    rows = await DailyReportSources().get_candles(
        symbol,
        outputsize=max_bars + BEFORE_PADDING_BARS,
        timeframe=timeframe,
        before=end_ts,
    )
    out = []
    for r in rows:
        try:
            t = int(r["time"])
        except (KeyError, TypeError, ValueError):
            continue
        if t + duration <= end_ts:
            out.append(r)
    return out[-max_bars:]


def _now_epoch() -> int:
    return int(time.time())


async def run_production_cycle(session, *, now: int | None = None) -> dict[str, Any]:
    """生产入口：SqlShadowRepo + production_market_loader，V1 隔离。

    负责整体提交（run_cycle_safe 内部失败也会写 heartbeat 错误并在此提交）；
    disabled/空 allowlist 时零 DB/零网络（不触发任何查询）。
    """
    core = ShadowCore.from_settings()
    if not core.enabled or not core.symbols:
        return {"enabled": False, "ingested": 0, "labeled": 0, "failed": 0,
                "decision_failed": 0, "decision_retried": 0}
    repo = SqlShadowRepo(session, core=core)
    result = await run_cycle_safe(
        core, repo, production_market_loader, now if now is not None else _now_epoch(),
    )
    await session.commit()
    return result
