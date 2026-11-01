from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, Text, Float, DateTime, ForeignKey, Integer, Boolean, BigInteger, UniqueConstraint, SmallInteger, CheckConstraint, Index, JSON, text as sa_text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MarketCandle(Base):
    __tablename__ = "market_candles"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "time", name="uq_market_candle"),)

    id:        Mapped[int]   = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol:    Mapped[str]   = mapped_column(String(30), nullable=False, index=True)
    timeframe: Mapped[str]   = mapped_column(String(10), nullable=False, index=True)
    time:      Mapped[int]   = mapped_column(BigInteger, nullable=False)
    open:      Mapped[float] = mapped_column(Float, nullable=False)
    high:      Mapped[float] = mapped_column(Float, nullable=False)
    low:       Mapped[float] = mapped_column(Float, nullable=False)
    close:     Mapped[float] = mapped_column(Float, nullable=False)
    volume:    Mapped[float] = mapped_column(Float, nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    watchlists: Mapped[list["Watchlist"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    replay_notes: Mapped[list["ReplayNote"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    alerts: Mapped[list["Alert"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Watchlist(Base):
    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(20), nullable=False)  # stock, etf, index, crypto
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="watchlists")


class ReplayNote(Base):
    __tablename__ = "replay_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    replay_start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    note_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    trend_view: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    support_level: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    resistance_level: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    entry_idea: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stop_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    take_profit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="replay_notes")


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    # price_above, price_below, rsi_above, rsi_below, ma_cross_up, ma_cross_down
    condition_type: Mapped[str] = mapped_column(String(30), nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False, default="1day")
    discord_webhook_url: Mapped[str] = mapped_column(Text, nullable=False)
    ma_period: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    repeating: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_triggered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    triggered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    # Exact completed candle/bucket last delivered by a repeating rule.
    last_triggered_signal_time: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="alerts")


class ChanlunSignal(Base):
    """Durable, deduplicated 缠论 signal and its delivery state."""

    __tablename__ = "chanlun_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    dedupe_key: Mapped[str] = mapped_column(String(240), unique=True, index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default="seg")
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    k: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    state: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    tier: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    signal_time: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    confirmed_time: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # pending, sent, failed, in_app_only
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class NotificationChannel(Base):
    """Server-side notification destination; plans reference it by id."""

    __tablename__ = "notification_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # discord today; feishu/email can be added through sender adapters.
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="discord")
    config: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class MonitorPlan(Base):
    """A dynamic-universe, parallel-detector monitoring configuration."""

    __tablename__ = "monitor_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    target_scope: Mapped[str] = mapped_column(String(20), nullable=False, default="watchlist")
    # Kept as the first selected timeframe for backwards compatibility with
    # older clients and rows. New code uses the JSON array in ``timeframes``.
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False, default="4h")
    timeframes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # JSON array: ma, macd, structure. Detectors run independently (OR).
    strategies: Mapped[str] = mapped_column(Text, nullable=False, default='["ma"]')
    parameters: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    channel_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("notification_channels.id"), nullable=True, index=True
    )
    repeating: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 指标订阅：JSON 数组，存 IndicatorVersion.id（只允许已晋升版本）。
    indicator_version_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 指标订阅游标：上次扫描到的 confirmed_at，避免重复推送（重启不丢）。
    indicator_cursor: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class AlertEvent(Base):
    """Durable, deduplicated signal event and its delivery state."""

    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dedupe_key: Mapped[str] = mapped_column(String(240), unique=True, index=True, nullable=False)
    plan_id: Mapped[int] = mapped_column(Integer, ForeignKey("monitor_plans.id"), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    strategy: Mapped[str] = mapped_column(String(30), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="signal")
    signal_time: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    reference_price: Mapped[float] = mapped_column(Float, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # pending, sent, failed, in_app_only
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class DailyReport(Base):
    """Durable, idempotent daily multi-market report and delivery state."""

    __tablename__ = "daily_reports"
    __table_args__ = (UniqueConstraint("report_date", "timezone", name="uq_daily_report_date_tz"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    report_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    market_dates: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    email_subject: Mapped[str] = mapped_column(String(240), nullable=False)
    email_text: Mapped[str] = mapped_column(Text, nullable=False)
    email_html: Mapped[str] = mapped_column(Text, nullable=False)
    discord_summary: Mapped[str] = mapped_column(Text, nullable=False)
    # pending, sent, failed, disabled
    email_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    discord_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    email_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discord_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    email_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    discord_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    discord_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class M3ShadowRuntime(Base):
    """M3B 影子运行时单例心跳表（id 恒为 1，CHECK 约束保证单例）。

    首次 enabled cycle 原子创建并固定 started_at（DB heartbeat 首次启用时刻），
    之后任何重跑/并发只更新 last_run_*，绝不改写 started_at；
    不对 V1 六表建任何外键，确保 M0 回滚不被本表阻塞。
    """

    __tablename__ = "m3_shadow_runtime"
    __table_args__ = (CheckConstraint("id = 1", name="ck_m3_shadow_runtime_singleton"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    started_at: Mapped[int] = mapped_column(BigInteger, nullable=False)  # epoch 秒
    last_run_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    last_run_ok: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, server_default=sa_text("now()"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, server_default=sa_text("now()"), onupdate=utcnow, nullable=False)


class M3ShadowEvent(Base):
    """M3B 影子事件表：一条 V1 alert_events 源行（source dedupe key）至多一行。

    - source_dedupe_key 唯一：同源重复/崩溃重跑不产生第二行、不重复计数；
    - 保存只读 source 快照、canonical M2 JSON + 16 位 digest、
      eligible/reasons/config hash、状态/attempt/backoff/错误、
      M3A label JSON / label_as_of / 成熟窗口 / next_label_at；
    - 不对 V1 六表建 FK（M0 回滚不被新行阻塞），source_event_id 仅作审计引用；
    - 状态机：pending → labeled → completed；rejected（decision 不 eligible）；
      failed（可退避重试）；failure_stage ∈ {decision, label}：decision 失败
      态允许 decision 字段为空（其余状态不允许），label 失败不得丢 decision。
    """

    __tablename__ = "m3_shadow_events"
    __table_args__ = (
        Index("ix_m3_shadow_events_symbol_signal", "symbol", "signal_time"),
        CheckConstraint(
            "status IN ('pending','rejected','labeled','completed','failed')",
            name="ck_m3_shadow_events_status",
        ),
        CheckConstraint("timeframe = '4h'", name="ck_m3_shadow_events_timeframe"),
        CheckConstraint("attempts >= 0", name="ck_m3_shadow_events_attempts"),
        CheckConstraint(
            "decision_digest IS NULL OR decision_digest ~ '^[0-9a-f]{16}$'",
            name="ck_m3_shadow_events_digest",
        ),
        CheckConstraint(
            "COALESCE(maturity_window IS NULL, FALSE) OR maturity_window IN (5,10,20,40)",
            name="ck_m3_shadow_events_maturity",
        ),
        # failure_stage 只在 failed 状态；failed 必须有 stage（COALESCE 防 NULL 放行）
        CheckConstraint(
            "COALESCE(failure_stage IS NULL AND status <> 'failed', FALSE) OR "
            "COALESCE(failure_stage IN ('decision','label') AND status = 'failed', FALSE)",
            name="ck_m3_shadow_events_failure_stage",
        ),
        # decision 完整性：failed/decision 必须决策五字段全 NULL；其余状态必须完整
        CheckConstraint(
            "(failure_stage = 'decision' "
            " AND decision_json IS NULL AND decision_digest IS NULL "
            " AND eligible IS NULL AND reasons IS NULL AND config_hash IS NULL) OR "
            "(COALESCE(failure_stage <> 'decision', TRUE) "
            " AND decision_json IS NOT NULL AND decision_digest IS NOT NULL "
            " AND eligible IS NOT NULL AND reasons IS NOT NULL AND config_hash IS NOT NULL)",
            name="ck_m3_shadow_events_decision_complete",
        ),
        # label_bound：pending 允许 censored（label_json+label_as_of 非空、
        # maturity NULL）；仍拒绝 maturity 非空却无 label_json/label_as_of
        CheckConstraint(
            "(label_json IS NULL AND label_as_of IS NULL AND maturity_window IS NULL) OR "
            "(label_json IS NOT NULL AND label_as_of IS NOT NULL "
            " AND (maturity_window IS NOT NULL OR status = 'pending'))",
            name="ck_m3_shadow_events_label_bound",
        ),
        # 状态机一致性（与 ShadowEventRow / validate_row_content 三层统一）
        CheckConstraint(
            "(status = 'failed' AND failure_stage = 'decision' AND eligible IS NULL "
            " AND label_json IS NULL AND label_as_of IS NULL AND maturity_window IS NULL) OR "
            "(status = 'failed' AND failure_stage = 'label' AND eligible = TRUE "
            " AND decision_json IS NOT NULL AND decision_digest IS NOT NULL "
            " AND reasons IS NOT NULL AND config_hash IS NOT NULL) OR "
            "(status = 'rejected' AND eligible = FALSE "
            " AND label_json IS NULL AND label_as_of IS NULL AND maturity_window IS NULL) OR "
            "(status = 'pending' AND eligible = TRUE "
            " AND decision_json IS NOT NULL AND decision_digest IS NOT NULL "
            " AND reasons IS NOT NULL AND config_hash IS NOT NULL "
            " AND (label_json IS NULL OR "
            "      (label_as_of IS NOT NULL AND maturity_window IS NULL))) OR "
            "(status = 'labeled' AND eligible = TRUE "
            " AND decision_json IS NOT NULL AND decision_digest IS NOT NULL "
            " AND reasons IS NOT NULL AND config_hash IS NOT NULL "
            " AND label_json IS NOT NULL AND label_as_of IS NOT NULL "
            " AND maturity_window IS NOT NULL AND maturity_window < 40) OR "
            "(status = 'completed' AND eligible = TRUE "
            " AND decision_json IS NOT NULL AND decision_digest IS NOT NULL "
            " AND reasons IS NOT NULL AND config_hash IS NOT NULL "
            " AND label_json IS NOT NULL AND label_as_of IS NOT NULL "
            " AND maturity_window = 40)",
            name="ck_m3_shadow_events_state_consistency",
        ),
        # 重试卫生：非 failed 必须 attempts=0、error 空，next_retry_at 仅
        # pending/labeled 缺数据 backoff 允许（rejected/completed 必须空）；
        # 任何 failed 必须 attempts>=1、next_retry_at 非空、last_error 非空白
        # （btrim 去空白后非空，防 NULL/空串放行）
        CheckConstraint(
            "(status <> 'failed' AND attempts = 0 AND last_error IS NULL "
            " AND (next_retry_at IS NULL OR status IN ('pending','labeled'))) "
            "OR (status = 'failed' AND attempts >= 1 AND next_retry_at IS NOT NULL "
            "    AND last_error IS NOT NULL AND btrim(last_error) <> '')",
            name="ck_m3_shadow_events_retry_state",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_dedupe_key: Mapped[str] = mapped_column(String(240), unique=True, index=True, nullable=False)
    source_event_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    signal_time: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    source_snapshot: Mapped[str] = mapped_column(Text, nullable=False)  # 只读 source 快照 JSON
    # decision 失败态（failure_stage='decision'）允许为空；其余状态必须完整
    decision_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # canonical M2 JSON
    decision_digest: Mapped[Optional[str]] = mapped_column(String(16), index=True, nullable=True)
    eligible: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    reasons: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    config_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # pending / rejected / labeled / completed / failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", server_default="pending", index=True)
    # decision（取数/计算失败）或 label（标签失败）；仅 failed 状态非空
    failure_stage: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_retry_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    label_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    label_as_of: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    maturity_window: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    next_label_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, server_default=sa_text("now()"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, server_default=sa_text("now()"), onupdate=utcnow, nullable=False)


class HotProductSnapshot(Base):
    """Raw daily hot-list candidates used for the rolling three-day score."""

    __tablename__ = "hot_product_snapshots"
    __table_args__ = (
        UniqueConstraint("market", "snapshot_date", "symbol", name="uq_hot_snapshot_market_date_symbol"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    snapshot_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    attention_score: Mapped[float] = mapped_column(Float, nullable=False)
    turnover: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    raw_payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


# --------------------------------------------------------------------------- #
# 指标 / 统计 / 实验 / 指标库（重构新增）
# --------------------------------------------------------------------------- #

# SQLite 测试环境需要 INTEGER 才能自增；Postgres 仍用 BIGINT
PK = BigInteger().with_variant(Integer, "sqlite")
FK = BigInteger().with_variant(Integer, "sqlite")


class IndicatorEvent(Base):
    """统一事件表：所有指标的历史触发都落在这里（统计、跳转、练习的数据源）。"""

    __tablename__ = "indicator_events"
    __table_args__ = (
        UniqueConstraint("uid", name="uq_indicator_event_uid"),
        Index("ix_indicator_event_lookup", "indicator_id", "symbol", "timeframe", "bar_time"),
        Index("ix_indicator_event_confirmed", "symbol", "timeframe", "confirmed_at"),
    )

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    uid: Mapped[str] = mapped_column(String(200), nullable=False)
    indicator_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    indicator_version_id: Mapped[Optional[int]] = mapped_column(FK, nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    bar_time: Mapped[int] = mapped_column(BigInteger, nullable=False)
    confirmed_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    first_seen_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    strength: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="confirmed")
    event_key: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    params_hash: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="ev1")
    evidence_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    # 投递状态：NULL = 还没成功推送（下次扫描会重试），非 NULL = 已送达时间戳
    notified_at: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)


class IndicatorOutcome(Base):
    """一次事件的结果（按 horizon / target_r / stop_lookback 区分口径）。"""

    __tablename__ = "indicator_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "event_id", "horizon", "target_r", "stop_lookback", name="uq_indicator_outcome_variant"
        ),
        Index("ix_indicator_outcome_event", "event_id"),
    )

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(FK, ForeignKey("indicator_events.id", ondelete="CASCADE"), nullable=False)
    horizon: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    target_r: Mapped[float] = mapped_column(Float, nullable=False, default=2.0)
    stop_lookback: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    entry_time: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_price: Mapped[float] = mapped_column(Float, nullable=False)
    target_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_time: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str] = mapped_column(String(16), nullable=False)
    r_multiple: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    mfe_r: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    mae_r: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bars_held: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    censored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)


class Experiment(Base):
    """实验 = 一份可 diff 的 spec，而不是一个脚本。"""

    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    spec_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    params_hash: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", index=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    runs: Mapped[list["ExperimentRun"]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan"
    )


class ExperimentRun(Base):
    __tablename__ = "experiment_runs"
    __table_args__ = (Index("ix_experiment_run_exp", "experiment_id", "created_at"),)

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    experiment_id: Mapped[int] = mapped_column(
        FK, ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    git_sha: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    data_version: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    metrics_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    artifact_dir: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    experiment: Mapped["Experiment"] = relationship(back_populates="runs")
    trades: Mapped[list["ExperimentTrade"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class ExperimentTrade(Base):
    __tablename__ = "experiment_trades"
    __table_args__ = (Index("ix_experiment_trade_run", "run_id", "entry_time"),)

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        FK, ForeignKey("experiment_runs.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    entry_time: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_time: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    r_multiple: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pnl_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    entry_event_uid: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    exit_event_uid: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    run: Mapped["ExperimentRun"] = relationship(back_populates="trades")


class Indicator(Base):
    """指标库里的「指标实例」（不是引擎 primitive）。"""

    __tablename__ = "indicators"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    primitive_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    current_version_id: Mapped[Optional[int]] = mapped_column(FK, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    deleted_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    versions: Mapped[list["IndicatorVersion"]] = relationship(
        back_populates="indicator", cascade="all, delete-orphan"
    )


class IndicatorVersion(Base):
    __tablename__ = "indicator_versions"
    __table_args__ = (
        UniqueConstraint("indicator_id", "version_no", name="uq_indicator_version_no"),
    )

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    indicator_id: Mapped[int] = mapped_column(
        FK, ForeignKey("indicators.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    spec_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    params_hash: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    engine_version: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    evidence_run_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    metrics_snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    promoted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    promoted_by: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    demoted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    indicator: Mapped["Indicator"] = relationship(back_populates="versions")


class PromotionAudit(Base):
    __tablename__ = "promotion_audit"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    indicator_id: Mapped[int] = mapped_column(FK, nullable=False, index=True)
    version_id: Mapped[Optional[int]] = mapped_column(FK, nullable=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    actor: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
