"""指标告警扫描：算事件 → 附历史统计 → 推送到通知渠道（飞书/Discord/邮件）。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import event_store
from app.core.indicator_alert import send_indicator_digest
from app.core.indicator_provider import get_registry
from app.core.market_data import read_local_bars
from app.db.session import get_db
from app.indicator_core.stats import summarize
from app.models.models import NotificationChannel

router = APIRouter()


class ScanRequest(BaseModel):
    channel_id: int
    symbol: str
    timeframe: str = "4h"
    indicators: list[str] | None = None
    since: int | None = Field(default=None, description="只推 confirmed_at >= since 的事件")
    limit: int = Field(default=10, ge=1, le=50)
    base_url: str = "http://localhost:3000"


@router.post("/indicator-scan")
async def indicator_scan(
    req: ScanRequest, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    channel = await db.get(NotificationChannel, req.channel_id)
    if channel is None or not channel.is_active:
        raise HTTPException(status_code=404, detail="通知渠道不存在或未启用")

    bars = list(read_local_bars(req.symbol, req.timeframe))
    if not bars:
        raise HTTPException(status_code=404, detail="没有本地行情数据")
    registry = get_registry()
    wanted = req.indicators or registry.names
    unknown = [item for item in wanted if item not in registry.names]
    if unknown:
        raise HTTPException(status_code=400, detail=f"未注册的指标: {unknown}")

    events = registry.run_many(wanted, bars, req.symbol, req.timeframe, {})
    if req.since is not None:
        events = [event for event in events if event.confirmed_at >= req.since]
    events = events[: req.limit]
    if not events:
        return {"count": 0, "sent": False}

    outcomes = await event_store.load_outcomes(
        db, symbol=req.symbol, timeframe=req.timeframe
    )
    by_indicator: dict[str, list[dict[str, Any]]] = {}
    for row in outcomes:
        by_indicator.setdefault(row["indicator_id"], []).append(row)
    summaries = {
        indicator_id: summarize(rows).to_dict() for indicator_id, rows in by_indicator.items()
    }

    sent = await send_indicator_digest(
        channel,
        symbol=req.symbol,
        timeframe=req.timeframe,
        events=events,
        summaries=summaries,
        base_url=req.base_url,
    )
    return {
        "count": len(events),
        "sent": sent,
        "indicators": sorted({event.indicator_id for event in events}),
    }
