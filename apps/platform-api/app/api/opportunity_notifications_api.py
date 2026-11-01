"""机会通知（System Opportunity Notifications）—— `alert_events` 的投递视图。

背景：前端右栏 `Rail` 与 `OpportunityNotificationFeed` 早已调用
`/api/opportunity-notifications*` 六个接口，但后端从未实现（浏览器实测每页 404）。
本模块把**既有的** `alert_events`（已含 pending/sent/failed/in_app_only 状态、
attempts/next_retry_at/sent_at/last_error/is_read 投递字段）按前端契约暴露出来，
不新增表、不改投递链路，投递与重试仍复用 `app.core.monitor_engine`。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.monitoring_api import DEFAULT_USER_ID
from app.core.config import settings
from app.core.monitor_engine import retry_monitor_deliveries, run_monitor_checks
from app.db.session import get_db
from app.models.models import AlertEvent, MonitorPlan, NotificationChannel, Watchlist

router = APIRouter()

MAX_LIMIT = 200


def _decode(raw: str | None, fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def _ma_periods(payload: dict) -> list[int]:
    """把 payload.parameters 归一化成前端渲染 `MA{n}` 需要的数字数组。"""
    params = payload.get("parameters") or {}
    if not isinstance(params, dict):
        return []
    for key in ("periods", "ma_periods"):
        value = params.get(key)
        if isinstance(value, list):
            return [int(item) for item in value if isinstance(item, (int, float))]
    for key in ("period", "ma"):
        value = params.get(key)
        if isinstance(value, (int, float)):
            return [int(value)]
    if isinstance(params.get("fast"), (int, float)) and isinstance(params.get("slow"), (int, float)):
        return [int(params["fast"]), int(params["slow"])]
    return []


def _serialize(event: AlertEvent) -> dict:
    payload = _decode(event.payload, {})
    periods = _ma_periods(payload)
    return {
        "id": event.id,
        "symbol": event.symbol,
        "strategy": event.strategy,
        "timeframe": event.timeframe,
        "signal_time": event.signal_time,
        "parameters": json.dumps(periods, ensure_ascii=False),
        "title": payload.get("summary") or event.event_type,
        "description": payload.get("description") or payload.get("detail") or "",
        "data_source": payload.get("data_source"),
        "status": event.status,
        "attempts": event.attempts,
        "next_retry_at": event.next_retry_at.isoformat() if event.next_retry_at else None,
        "sent_at": event.sent_at.isoformat() if event.sent_at else None,
        "last_error": event.last_error,
        "is_read": event.is_read,
        "simulated": bool(payload.get("simulated", False)),
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "updated_at": event.updated_at.isoformat() if event.updated_at else None,
    }


@router.get("")
async def list_opportunity_notifications(
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
    status_filter: Annotated[Optional[str], Query(alias="status")] = None,
    unread_only: bool = False,
    db: AsyncSession = Depends(get_db),
) -> dict:
    conditions = []
    if status_filter:
        conditions.append(AlertEvent.status == status_filter)
    if unread_only:
        conditions.append(AlertEvent.is_read.is_(False))

    stmt = select(AlertEvent).order_by(AlertEvent.signal_time.desc(), AlertEvent.id.desc()).limit(limit)
    if conditions:
        stmt = stmt.where(*conditions)
    rows = list((await db.execute(stmt)).scalars().all())

    total = await db.scalar(select(func.count()).select_from(AlertEvent))
    unread = await db.scalar(
        select(func.count()).select_from(AlertEvent).where(AlertEvent.is_read.is_(False))
    )
    return {"items": [_serialize(row) for row in rows], "total": int(total or 0), "unread": int(unread or 0)}


@router.get("/summary")
async def opportunity_notification_summary(db: AsyncSession = Depends(get_db)) -> dict:
    plans = list(
        (
            await db.execute(select(MonitorPlan).where(MonitorPlan.is_active.is_(True)))  # noqa: E712
        ).scalars().all()
    )
    channels = list(
        (
            await db.execute(
                select(NotificationChannel).where(
                    NotificationChannel.is_active.is_(True),  # noqa: E712
                    NotificationChannel.kind == "discord",
                )
            )
        ).scalars().all()
    )
    symbols = list(
        (
            await db.execute(
                select(Watchlist.symbol)
                .where(Watchlist.user_id == DEFAULT_USER_ID)
                .order_by(Watchlist.created_at)
            )
        ).scalars().all()
    )
    status_rows = await db.execute(select(AlertEvent.status, func.count()).group_by(AlertEvent.status))

    ma_periods: list[int] = []
    for plan in plans:
        ma_periods.extend(_ma_periods({"parameters": _decode(plan.parameters, {})}))
    return {
        "enabled": bool(settings.INDICATOR_ALERTS_ENABLED or settings.CHANLUN_ALERTS_ENABLED),
        "discord_configured": bool(channels),
        "symbols": [str(symbol).upper() for symbol in symbols],
        "ma_periods": sorted(set(ma_periods)),
        "timeframe": plans[0].timeframe if plans else "4h",
        "counts": {str(row[0]): int(row[1]) for row in status_rows},
        "schedules": [],
    }


@router.post("/scan-now")
async def scan_opportunities_now(db: AsyncSession = Depends(get_db)) -> dict:
    result = await run_monitor_checks(db)
    await db.commit()
    return {"scanned": True, **result}


@router.post("/simulate")
async def simulate_opportunity_notification(body: dict, db: AsyncSession = Depends(get_db)) -> dict:
    """写入一条标记 simulated 的站内通知，用于验证卡片渲染与投递链路。"""
    symbol = str(body.get("symbol") or "").upper()
    if not symbol:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="symbol 必填")
    plan = (
        await db.execute(select(MonitorPlan).where(MonitorPlan.is_active.is_(True)).limit(1))  # noqa: E712
    ).scalars().first()
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="没有启用的监控计划，无法模拟投递；请先在「告警 → 监控计划」创建",
        )
    periods = [int(item) for item in (body.get("parameters") or []) if isinstance(item, (int, float))]
    event = AlertEvent(
        dedupe_key=f"sim-{symbol}-{uuid.uuid4().hex[:12]}",
        plan_id=plan.id,
        symbol=symbol,
        timeframe=plan.timeframe,
        strategy="ma",
        event_type="ma_breakout",
        direction="long",
        severity="signal",
        signal_time=int(datetime.now(tz=timezone.utc).timestamp()),
        reference_price=float(body.get("reference_price") or 0.0),
        payload=json.dumps(
            {
                "summary": f"{symbol} 模拟机会通知",
                "description": "由 /simulate 生成的模拟卡片",
                "parameters": {"periods": periods} if periods else {},
                "simulated": True,
            },
            ensure_ascii=False,
        ),
        status="pending",
        next_retry_at=datetime.now(tz=timezone.utc),
    )
    db.add(event)
    await db.commit()
    if body.get("send_external"):
        await retry_monitor_deliveries(db)
        await db.commit()
        await db.refresh(event)
    return _serialize(event)


@router.post("/{notification_id}/read")
async def mark_opportunity_notification_read(notification_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    event = await db.get(AlertEvent, notification_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="通知不存在")
    event.is_read = True
    await db.commit()
    await db.refresh(event)
    return _serialize(event)


@router.post("/{notification_id}/retry")
async def retry_opportunity_notification(notification_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    event = await db.get(AlertEvent, notification_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="通知不存在")
    if event.status == "sent":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该通知已投递成功，无需重试")
    event.status = "pending"
    event.next_retry_at = datetime.now(tz=timezone.utc)
    event.last_error = None
    await db.commit()
    await retry_monitor_deliveries(db)
    await db.commit()
    await db.refresh(event)
    return _serialize(event)
