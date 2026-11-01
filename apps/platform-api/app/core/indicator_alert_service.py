"""指标告警扫描服务（scheduler / CLI / API 共用）。

去重不靠内存：先把事件写入 ``indicator_events``（uid 幂等），
只对「库里原本没有、且落在回看窗口内」的事件发通知。重启后不会重复轰炸。
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import event_store
from app.core.indicator_alert import send_indicator_digest_all
from app.core.indicator_provider import get_registry
from app.core.market_data import read_local_bars
from app.indicator_core.stats import summarize
from app.models.models import (
    Indicator,
    IndicatorVersion,
    MonitorPlan,
    NotificationChannel,
    Watchlist,
)


logger = logging.getLogger(__name__)


async def _active_channels(db: AsyncSession) -> list[NotificationChannel]:
    rows = await db.execute(
        select(NotificationChannel).where(NotificationChannel.is_active.is_(True))
    )
    return list(rows.scalars().all())


async def scan_and_notify(
    db: AsyncSession,
    symbols: Sequence[str],
    timeframes: Sequence[str],
    *,
    lookback_hours: int = 8,
    base_url: str = "http://localhost:3000",
    channels: Sequence[Any] | None = None,
    indicator_ids: Sequence[str] | None = None,
    min_events: int = 1,
) -> dict[str, Any]:
    registry = get_registry()
    wanted = list(indicator_ids) if indicator_ids else registry.names
    unknown = [item for item in wanted if item not in registry.names]
    if unknown:
        raise ValueError(f"未注册的指标: {unknown}")

    since = int(time.time()) - max(1, lookback_hours) * 3600
    scanned = inserted = 0

    for symbol in symbols:
        for timeframe in timeframes:
            bars = list(read_local_bars(symbol, timeframe))
            if not bars:
                continue
            scanned += 1
            events = registry.run_many(wanted, bars, symbol, timeframe, {})
            recent = [event for event in events if event.confirmed_at >= since]
            if recent:
                inserted += await event_store.upsert_events(db, recent)

    # 待推送 = 窗口内 notified_at 为空的事件：新插入的 + 上次投递失败留下的
    pending: list[Any] = []
    for symbol in symbols:
        for timeframe in timeframes:
            pending.extend(
                await event_store.load_pending_notifications(
                    db, symbol=symbol, timeframe=timeframe, since=since
                )
            )

    if channels is None:
        channels = await _active_channels(db)
    if not channels:
        return {"scanned": scanned, "inserted": inserted, "pending": len(pending), "sent": False}

    sent_any = False
    notified = 0
    by_key: dict[tuple[str, str], list[Any]] = {}
    for event in pending:
        by_key.setdefault((event.symbol, event.timeframe), []).append(event)

    for (symbol, timeframe), events in by_key.items():
        outcomes = await event_store.load_outcomes(db, symbol=symbol, timeframe=timeframe)
        by_indicator: dict[str, list[dict[str, Any]]] = {}
        for row in outcomes:
            by_indicator.setdefault(row["indicator_id"], []).append(row)
        summaries = {
            key: summarize(rows).to_dict() for key, rows in by_indicator.items()
        }
        try:
            sent = await send_indicator_digest_all(
                channels,
                symbol=symbol,
                timeframe=timeframe,
                events=events,
                summaries=summaries,
                base_url=base_url,
            )
        except Exception:  # 429/超时/网络异常：当作投递失败，留给下一轮重试
            logger.exception("指标告警投递失败（将重试）: %s %s", symbol, timeframe)
            sent = False
        if sent:
            # 只有成功送达才记账；失败保持 NULL，下一次扫描自动重试（幂等：不会重复发已送达的）
            notified += await event_store.mark_notified(db, [event.uid for event in events])
        sent_any = sent_any or sent

    return {
        "scanned": scanned,
        "inserted": inserted,
        "pending": len(pending),
        "notified": notified,
        "sent": sent_any,
    }


async def _indicator_summaries(
    db: AsyncSession, symbol: str, timeframe: str
) -> dict[str, dict[str, Any]]:
    outcomes = await event_store.load_outcomes(db, symbol=symbol, timeframe=timeframe)
    by_indicator: dict[str, list[dict[str, Any]]] = {}
    for row in outcomes:
        by_indicator.setdefault(row["indicator_id"], []).append(row)
    return {key: summarize(rows).to_dict() for key, rows in by_indicator.items()}


def _plan_timeframes(plan: Any) -> list[str]:
    try:
        parsed = json.loads(plan.timeframes) if plan.timeframes else []
    except (TypeError, ValueError):
        parsed = []
    if isinstance(parsed, list) and parsed:
        return [str(item) for item in parsed]
    return [str(plan.timeframe or "4h")]


async def scan_plan_subscriptions(
    db: AsyncSession,
    *,
    lookback_hours: int = 8,
    base_url: str = "http://localhost:3000",
    channels: Sequence[Any] | None = None,
    limit_per_plan: int = 50,
) -> dict[str, Any]:
    """按监控计划订阅的指标版本扫描；游标按计划持久化，重启不重复推送。"""
    registry = get_registry()
    plans = list(
        (
            await db.execute(
                select(MonitorPlan).where(
                    MonitorPlan.is_active.is_(True),
                    MonitorPlan.indicator_version_ids.isnot(None),
                    MonitorPlan.indicator_version_ids != "[]",
                )
            )
        )
        .scalars()
        .all()
    )
    if not plans:
        return {"plans": 0, "events": 0, "sent": False}

    now = int(time.time())
    total_events = 0
    sent_any = False

    for plan in plans:
        try:
            version_ids = [
                int(item) for item in json.loads(plan.indicator_version_ids or "[]")
            ]
        except (TypeError, ValueError):
            version_ids = []
        if not version_ids:
            continue

        rows = await db.execute(
            select(IndicatorVersion, Indicator)
            .join(Indicator, Indicator.id == IndicatorVersion.indicator_id)
            .where(IndicatorVersion.id.in_(version_ids))
        )
        targets: list[tuple[str, dict[str, Any]]] = []
        for version, indicator in rows.all():
            if version.promoted_at is None or version.demoted_at is not None:
                continue
            if indicator.deleted_at is not None:
                continue
            params = dict((version.spec_json or {}).get("params") or {})
            targets.append((indicator.primitive_id, params))
        if not targets:
            continue

        symbols = list(
            dict.fromkeys(
                str(value).upper()
                for value in (
                    await db.execute(
                        select(Watchlist.symbol)
                        .where(Watchlist.user_id == plan.user_id)
                        .order_by(Watchlist.created_at)
                    )
                )
                .scalars()
                .all()
            )
        )
        if not symbols:
            plan.last_error = "自选列表为空"
            continue

        since = plan.indicator_cursor if plan.indicator_cursor is not None else now - max(1, lookback_hours) * 3600
        channel = (
            await db.get(NotificationChannel, plan.channel_id) if plan.channel_id else None
        )
        send_channels = [channel] if channel is not None else channels
        new_cursor = since
        advanced = False

        try:
            for primitive_id, params in targets:
                for symbol in symbols:
                    for timeframe in _plan_timeframes(plan):
                        bars = list(read_local_bars(symbol, timeframe))
                        if not bars:
                            continue
                        events = registry.run_many(
                            [primitive_id], bars, symbol, timeframe, {primitive_id: params}
                        )
                        fresh = [event for event in events if event.confirmed_at > since]
                        if not fresh:
                            continue
                        await event_store.upsert_events(db, fresh)
                        summaries = await _indicator_summaries(db, symbol, timeframe)
                        batch = fresh[:limit_per_plan]
                        try:
                            sent = await send_indicator_digest_all(
                                send_channels,
                                symbol=symbol,
                                timeframe=timeframe,
                                events=batch,
                                summaries=summaries,
                                base_url=base_url,
                            )
                        except Exception:  # 429/超时：当作失败，游标不前进，下一轮重试
                            logger.exception("计划订阅投递失败（将重试）: plan=%s", plan.id)
                            sent = False
                        sent_any = sent_any or sent
                        if sent:
                            # 标记已送达，避免全局兜底扫描重复推送同一事件
                            await event_store.mark_notified(db, [event.uid for event in batch])
                            total_events += len(batch)
                            new_cursor = max(
                                new_cursor, max(event.confirmed_at for event in batch)
                            )
                            advanced = True
            if advanced:
                plan.indicator_cursor = new_cursor
            plan.last_run_at = datetime.now(timezone.utc).replace(tzinfo=None)
            plan.last_error = None
        except Exception as exc:  # 单计划失败不影响其他计划
            plan.last_error = f"{type(exc).__name__}: {exc}"[:500]

    await db.commit()
    return {"plans": len(plans), "events": total_events, "sent": sent_any}
