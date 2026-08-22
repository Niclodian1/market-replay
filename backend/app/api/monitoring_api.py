from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.notification_channels import (
    apply_email_provider_defaults,
    decode_channel_config,
    encode_channel_config,
    email_config_is_complete,
    email_error_hint,
    mask_endpoint,
    safe_channel_config,
    send_email_message,
    send_to_channel,
)
from app.core.daily_report_delivery import environment_email_config
from app.core.monitor_engine import run_monitor_checks
from app.db.session import get_db
from app.models.models import AlertEvent, MonitorPlan, NotificationChannel, User
from app.schemas.monitoring import (
    AlertEventOut,
    AlertEventsResponse,
    ChannelCreate,
    ChannelOut,
    ChannelsResponse,
    ChannelUpdate,
    MonitorPlanCreate,
    MonitorPlanOut,
    MonitorPlansResponse,
    MonitorPlanUpdate,
)


router = APIRouter()
DEFAULT_USER_ID = 1
VALID_TIMEFRAMES = {"1h", "4h", "1day"}
VALID_STRATEGIES = {"ma", "macd", "structure"}
VALID_CHANNEL_KINDS = {"discord", "feishu", "email"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _ensure_default_user(db: AsyncSession) -> None:
    result = await db.execute(select(User).where(User.id == DEFAULT_USER_ID))
    if result.scalar_one_or_none() is None:
        db.add(User(id=DEFAULT_USER_ID, username="default", password_hash="unused"))
        await db.commit()


def _normalize_timeframes(timeframes: list[str]) -> list[str]:
    normalized = list(dict.fromkeys(str(item).lower() for item in timeframes))
    if not normalized or not set(normalized).issubset(VALID_TIMEFRAMES):
        raise HTTPException(400, f"周期必须从 {', '.join(sorted(VALID_TIMEFRAMES))} 中选择至少一个")
    return normalized


def _validate_plan(
    *,
    target_scope: str,
    strategies: list[str],
    timeframes: list[str] | None = None,
    timeframe: str | None = None,
) -> list[str]:
    if target_scope != "watchlist":
        raise HTTPException(400, "第一阶段 target_scope 仅支持动态自选列表 watchlist")
    # ``timeframe`` keeps direct callers and old clients compatible.
    _normalize_timeframes(timeframes if timeframes is not None else ([timeframe] if timeframe else []))
    normalized = list(dict.fromkeys(str(item).lower() for item in strategies))
    if not normalized or not set(normalized).issubset(VALID_STRATEGIES):
        raise HTTPException(400, f"策略必须从 {', '.join(sorted(VALID_STRATEGIES))} 中选择至少一个")
    return normalized


def _channel_out(channel: NotificationChannel, *, source: str = "database") -> ChannelOut:
    config = decode_channel_config(channel.config)
    return ChannelOut(
        id=channel.id,
        name=channel.name,
        kind=channel.kind,
        source=source,
        masked_endpoint=mask_endpoint(channel.kind, config),
        settings=safe_channel_config(channel.kind, config),
        is_default=channel.is_default,
        is_active=channel.is_active,
        created_at=channel.created_at,
        updated_at=channel.updated_at,
    )


def _validated_channel_config(
    kind: str,
    config: dict,
    *,
    existing: dict | None = None,
) -> dict:
    value = dict(config)
    if kind in {"discord", "feishu"}:
        if not str(value.get("webhook_url") or "").startswith("https://"):
            raise HTTPException(400, "Webhook 必须是有效的 HTTPS URL")
        return value

    if kind != "email":
        raise HTTPException(400, "当前仅支持 Discord 和 Email")

    value = apply_email_provider_defaults(value)
    if not str(value.get("smtp_password") or "").strip() and existing:
        value["smtp_password"] = existing.get("smtp_password")
    host = str(value.get("smtp_host") or "").strip()
    username = str(value.get("smtp_username") or "").strip()
    from_address = str(value.get("from_address") or "").strip()
    raw_recipients = value.get("to_addresses") or value.get("address") or ""
    recipients = raw_recipients if isinstance(raw_recipients, list) else str(raw_recipients).split(",")
    recipients = [str(item).strip() for item in recipients if str(item).strip()]
    try:
        port = int(value.get("smtp_port") or 587)
    except (TypeError, ValueError):
        raise HTTPException(400, "SMTP 端口必须是数字") from None
    if not host or not 1 <= port <= 65535:
        raise HTTPException(400, "请填写有效的 SMTP 主机和端口")
    if "@" not in from_address or any("@" not in item for item in recipients):
        raise HTTPException(400, "请填写有效的发件人和收件人邮箱")
    use_ssl = bool(value.get("smtp_use_ssl", False))
    use_tls = bool(value.get("smtp_use_tls", True))
    if use_ssl and use_tls:
        raise HTTPException(400, "SSL 与 STARTTLS 只能选择一种")
    if username and not str(value.get("smtp_password") or ""):
        raise HTTPException(400, "已填写 SMTP 用户名，请同时填写密码或应用专用密码")
    return {
        "smtp_host": host,
        "smtp_port": port,
        "smtp_username": username,
        "smtp_password": str(value.get("smtp_password") or ""),
        "smtp_use_tls": use_tls,
        "smtp_use_ssl": use_ssl,
        "from_address": from_address,
        "to_addresses": recipients,
    }


async def _set_only_default(db: AsyncSession, channel_id: int) -> None:
    await db.execute(
        update(NotificationChannel)
        .where(NotificationChannel.user_id == DEFAULT_USER_ID)
        .values(is_default=False)
    )
    await db.execute(
        update(NotificationChannel)
        .where(NotificationChannel.id == channel_id, NotificationChannel.user_id == DEFAULT_USER_ID)
        .values(is_default=True)
    )


@router.get("/channels", response_model=ChannelsResponse)
async def list_channels(db: AsyncSession = Depends(get_db)):
    await _ensure_default_user(db)
    result = await db.execute(
        select(NotificationChannel)
        .where(NotificationChannel.user_id == DEFAULT_USER_ID)
        .order_by(NotificationChannel.is_default.desc(), NotificationChannel.created_at)
    )
    channels = list(result.scalars().all())
    items = [_channel_out(item) for item in channels]

    # SMTP settings in .env are the legacy daily-report fallback and are not
    # stored in notification_channels. Expose a safe, read-only row so users
    # can see and test the effective configuration from the UI.
    env_config = environment_email_config()
    has_environment_email = any(
        str(env_config.get(key) or "").strip()
        for key in ("smtp_host", "from_address", "to_addresses")
    )
    if has_environment_email:
        now = _utcnow().replace(tzinfo=timezone.utc)
        items.append(ChannelOut(
            id=0,
            name="环境变量 Email（日报回退）",
            kind="email",
            source="environment",
            masked_endpoint=mask_endpoint("email", env_config),
            settings=safe_channel_config("email", env_config),
            is_default=False,
            is_active=email_config_is_complete(env_config),
            created_at=now,
            updated_at=now,
        ))
    return ChannelsResponse(items=items)


@router.post("/channels", response_model=ChannelOut, status_code=status.HTTP_201_CREATED)
async def create_channel(body: ChannelCreate, db: AsyncSession = Depends(get_db)):
    await _ensure_default_user(db)
    kind = body.kind.lower()
    if kind not in VALID_CHANNEL_KINDS:
        raise HTTPException(400, "当前仅支持 Discord、飞书 和 Email")
    config = _validated_channel_config(kind, body.config)
    count = await db.scalar(
        select(func.count(NotificationChannel.id)).where(NotificationChannel.user_id == DEFAULT_USER_ID)
    )
    channel = NotificationChannel(
        user_id=DEFAULT_USER_ID,
        name=body.name.strip(),
        kind=kind,
        config=encode_channel_config(config),
        is_default=body.is_default or not count,
    )
    db.add(channel)
    await db.flush()
    if channel.is_default:
        await _set_only_default(db, channel.id)
    await db.commit()
    await db.refresh(channel)
    return _channel_out(channel)


@router.put("/channels/{channel_id}", response_model=ChannelOut)
async def update_channel(channel_id: int, body: ChannelUpdate, db: AsyncSession = Depends(get_db)):
    channel = await db.get(NotificationChannel, channel_id)
    if channel is None or channel.user_id != DEFAULT_USER_ID:
        raise HTTPException(404, "通知渠道不存在")
    if body.name is not None:
        channel.name = body.name.strip()
    if body.config is not None:
        config = _validated_channel_config(
            channel.kind,
            body.config,
            existing=decode_channel_config(channel.config),
        )
        channel.config = encode_channel_config(config)
    if body.is_active is not None:
        channel.is_active = body.is_active
    if body.is_default:
        await _set_only_default(db, channel.id)
    channel.updated_at = _utcnow()
    await db.commit()
    await db.refresh(channel)
    return _channel_out(channel)


@router.post("/channels/{channel_id}/test")
async def test_channel(channel_id: int, db: AsyncSession = Depends(get_db)):
    channel = await db.get(NotificationChannel, channel_id)
    if channel is None or channel.user_id != DEFAULT_USER_ID:
        raise HTTPException(404, "通知渠道不存在")
    ok = await send_to_channel(
        channel,
        "通知渠道测试",
        "Market Replay 已成功连接该服务器端通知渠道。",
    )
    if not ok:
        raise HTTPException(502, "通知发送失败，请检查渠道配置")
    return {"success": True}


@router.post("/email/test")
async def test_environment_email():
    """Send a test mail using the configured environment fallback."""
    config = environment_email_config()
    if not email_config_is_complete(config):
        raise HTTPException(400, "服务器环境变量中的 Email 配置不完整")
    try:
        await send_email_message(
            config,
            "[Market Replay] 邮件渠道测试",
            "这是一封 Market Replay 测试邮件。服务器环境变量 Email 配置可正常发送。",
        )
    except Exception as exc:
        raise HTTPException(502, email_error_hint(exc)) from exc
    return {"success": True}


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(channel_id: int, db: AsyncSession = Depends(get_db)):
    channel = await db.get(NotificationChannel, channel_id)
    if channel is None or channel.user_id != DEFAULT_USER_ID:
        raise HTTPException(404, "通知渠道不存在")
    used = await db.scalar(select(func.count(MonitorPlan.id)).where(MonitorPlan.channel_id == channel_id))
    if used:
        raise HTTPException(409, "该渠道仍被监控计划使用，请先改为继承默认渠道")
    await db.delete(channel)
    await db.commit()


async def _plan_out(db: AsyncSession, plan: MonitorPlan) -> MonitorPlanOut:
    channel = await db.get(NotificationChannel, plan.channel_id) if plan.channel_id else None
    try:
        strategies = json.loads(plan.strategies)
        parameters = json.loads(plan.parameters)
    except ValueError:
        strategies, parameters = [], {}
    stored_timeframes = None
    if plan.timeframes:
        try:
            parsed = json.loads(plan.timeframes)
            stored_timeframes = parsed if isinstance(parsed, list) else None
        except (TypeError, ValueError):
            pass
    try:
        timeframes = _normalize_timeframes(stored_timeframes or [plan.timeframe])
    except HTTPException:
        timeframes = ["4h"]
    return MonitorPlanOut(
        id=plan.id,
        name=plan.name,
        target_scope=plan.target_scope,
        timeframes=timeframes,
        timeframe=timeframes[0],
        strategies=strategies,
        parameters=parameters,
        channel_id=plan.channel_id,
        channel_name=channel.name if channel else None,
        repeating=plan.repeating,
        is_active=plan.is_active,
        last_run_at=plan.last_run_at,
        last_error=plan.last_error,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


@router.get("/plans", response_model=MonitorPlansResponse)
async def list_plans(db: AsyncSession = Depends(get_db)):
    await _ensure_default_user(db)
    result = await db.execute(
        select(MonitorPlan)
        .where(MonitorPlan.user_id == DEFAULT_USER_ID)
        .order_by(MonitorPlan.created_at.desc())
    )
    return MonitorPlansResponse(items=[await _plan_out(db, item) for item in result.scalars().all()])


@router.post("/plans", response_model=MonitorPlanOut, status_code=status.HTTP_201_CREATED)
async def create_plan(body: MonitorPlanCreate, db: AsyncSession = Depends(get_db)):
    await _ensure_default_user(db)
    requested_timeframes = (
        body.timeframes
        if body.timeframes is not None
        else ([body.timeframe] if body.timeframe is not None else ["4h"])
    )
    timeframes = _normalize_timeframes(requested_timeframes)
    strategies = _validate_plan(
        target_scope=body.target_scope,
        timeframes=timeframes,
        strategies=body.strategies,
    )
    if body.channel_id is not None:
        channel = await db.get(NotificationChannel, body.channel_id)
        if channel is None or channel.user_id != DEFAULT_USER_ID:
            raise HTTPException(400, "所选通知渠道不存在")
    plan = MonitorPlan(
        user_id=DEFAULT_USER_ID,
        name=body.name.strip(),
        target_scope=body.target_scope,
        timeframe=timeframes[0],
        timeframes=json.dumps(timeframes),
        strategies=json.dumps(strategies),
        parameters=json.dumps(body.parameters),
        channel_id=body.channel_id,
        repeating=True,
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return await _plan_out(db, plan)


@router.put("/plans/{plan_id}", response_model=MonitorPlanOut)
async def update_plan(plan_id: int, body: MonitorPlanUpdate, db: AsyncSession = Depends(get_db)):
    plan = await db.get(MonitorPlan, plan_id)
    if plan is None or plan.user_id != DEFAULT_USER_ID:
        raise HTTPException(404, "监控计划不存在")
    current_strategies = json.loads(plan.strategies)
    strategies = body.strategies if body.strategies is not None else current_strategies
    if "timeframes" in body.model_fields_set:
        requested_timeframes = body.timeframes or []
    elif "timeframe" in body.model_fields_set:
        requested_timeframes = [body.timeframe] if body.timeframe else []
    elif plan.timeframes:
        try:
            parsed_timeframes = json.loads(plan.timeframes)
            requested_timeframes = parsed_timeframes if isinstance(parsed_timeframes, list) else [plan.timeframe]
        except (TypeError, ValueError):
            requested_timeframes = [plan.timeframe]
    else:
        requested_timeframes = [plan.timeframe]
    timeframes = _normalize_timeframes(requested_timeframes)
    normalized = _validate_plan(
        target_scope=plan.target_scope,
        timeframes=timeframes,
        strategies=strategies,
    )
    if body.name is not None:
        plan.name = body.name.strip()
    plan.timeframe = timeframes[0]
    plan.timeframes = json.dumps(timeframes)
    plan.strategies = json.dumps(normalized)
    if body.parameters is not None:
        plan.parameters = json.dumps(body.parameters)
    if body.use_default_channel:
        plan.channel_id = None
    elif "channel_id" in body.model_fields_set:
        if body.channel_id is not None:
            channel = await db.get(NotificationChannel, body.channel_id)
            if channel is None or channel.user_id != DEFAULT_USER_ID:
                raise HTTPException(400, "所选通知渠道不存在")
        plan.channel_id = body.channel_id
    if body.is_active is not None:
        plan.is_active = body.is_active
    plan.updated_at = _utcnow()
    await db.commit()
    await db.refresh(plan)
    return await _plan_out(db, plan)


@router.delete("/plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(plan_id: int, db: AsyncSession = Depends(get_db)):
    plan = await db.get(MonitorPlan, plan_id)
    if plan is None or plan.user_id != DEFAULT_USER_ID:
        raise HTTPException(404, "监控计划不存在")
    await db.execute(delete(AlertEvent).where(AlertEvent.plan_id == plan.id))
    await db.delete(plan)
    await db.commit()


def _event_out(event: AlertEvent) -> AlertEventOut:
    try:
        payload = json.loads(event.payload)
    except ValueError:
        payload = {}
    return AlertEventOut(
        id=event.id, plan_id=event.plan_id, symbol=event.symbol,
        timeframe=event.timeframe, strategy=event.strategy,
        event_type=event.event_type, direction=event.direction,
        severity=event.severity, signal_time=event.signal_time,
        reference_price=event.reference_price, payload=payload,
        status=event.status, attempts=event.attempts, sent_at=event.sent_at,
        last_error=event.last_error, is_read=event.is_read, created_at=event.created_at,
    )


@router.get("/events", response_model=AlertEventsResponse)
async def list_events(limit: int = 100, db: AsyncSession = Depends(get_db)):
    limit = min(max(limit, 1), 500)
    plan_ids = select(MonitorPlan.id).where(MonitorPlan.user_id == DEFAULT_USER_ID)
    result = await db.execute(
        select(AlertEvent)
        .where(AlertEvent.plan_id.in_(plan_ids))
        .order_by(AlertEvent.created_at.desc())
        .limit(limit)
    )
    items = list(result.scalars().all())
    total = await db.scalar(select(func.count(AlertEvent.id)).where(AlertEvent.plan_id.in_(plan_ids))) or 0
    unread = await db.scalar(
        select(func.count(AlertEvent.id)).where(AlertEvent.plan_id.in_(plan_ids), AlertEvent.is_read == False)  # noqa: E712
    ) or 0
    return AlertEventsResponse(items=[_event_out(item) for item in items], total=total, unread=unread)


@router.post("/events/{event_id}/read")
async def mark_event_read(event_id: int, db: AsyncSession = Depends(get_db)):
    event = await db.get(AlertEvent, event_id)
    if event is None:
        raise HTTPException(404, "信号事件不存在")
    plan = await db.get(MonitorPlan, event.plan_id)
    if plan is None or plan.user_id != DEFAULT_USER_ID:
        raise HTTPException(404, "信号事件不存在")
    event.is_read = True
    await db.commit()
    return {"success": True}


@router.get("/capabilities")
async def monitoring_capabilities():
    return {
        "target_scopes": ["watchlist"],
        "timeframes": ["1h", "4h", "1day"],
        "strategies": [
            {"id": "ma", "label": "MA突破与回踩", "default": True},
            {"id": "macd", "label": "MACD动量观察与背离确认", "default": False},
            {"id": "structure", "label": "趋势线与收敛结构", "default": False},
        ],
        "execution": "parallel_or",
        "timeframe_selection": "multiple",
        "confirmation": "closed_bar",
        "repeating": True,
        "channel_kinds": ["discord", "feishu", "email"],
    }


@router.post("/run-now")
async def run_monitoring_now(db: AsyncSession = Depends(get_db)):
    return await run_monitor_checks(db)
