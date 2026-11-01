"""事件库：回填、写入、读取、标注。

DB 里只存「事实」（Event / Outcome），指标计算永远发生在 ``indicator_core``。
因此换引擎、改参数都不会污染历史：``params_hash`` + ``indicator_id`` 一起构成命名空间。
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

import time

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.indicator_provider import get_registry
from app.core.market_data import read_local_bars
from app.indicator_core import Event
from app.indicator_core.outcomes import Outcome, label_events
from app.models.models import IndicatorEvent, IndicatorOutcome

_DEFAULT_LABEL = {"horizon": 20, "target_r": 2.0, "stop_lookback": 20}


def _row_from_event(event: Event) -> dict[str, Any]:
    return {
        "uid": event.uid,
        "indicator_id": event.indicator_id,
        "symbol": event.symbol.upper(),
        "timeframe": event.timeframe,
        "direction": event.direction,
        "bar_time": event.bar_time,
        "confirmed_at": event.confirmed_at,
        "first_seen_at": event.first_seen_at or event.confirmed_at,
        "price": event.price,
        "strength": event.strength,
        "state": event.state,
        "event_key": event.event_key,
        "params_hash": event.params_hash,
        "schema_version": event.schema_version,
        "evidence_json": event.evidence,
    }


def build_events(
    symbol: str,
    timeframe: str,
    bars: Sequence[dict[str, Any]],
    indicator_ids: Iterable[str] | None = None,
    params: dict[str, dict[str, Any]] | None = None,
) -> list[Event]:
    registry = get_registry()
    ids = list(indicator_ids) if indicator_ids else registry.names
    return registry.run_many(ids, bars, symbol, timeframe, params or {})


async def upsert_events(db: AsyncSession, events: Sequence[Event]) -> int:
    """按 uid 幂等写入；返回新增条数。"""
    if not events:
        return 0
    rows = [_row_from_event(event) for event in events]
    uids = [row["uid"] for row in rows]
    existing: set[str] = set()
    for chunk_start in range(0, len(uids), 500):
        chunk = uids[chunk_start : chunk_start + 500]
        result = await db.execute(select(IndicatorEvent.uid).where(IndicatorEvent.uid.in_(chunk)))
        existing.update(result.scalars().all())

    fresh = [row for row in rows if row["uid"] not in existing]
    if fresh:
        db.add_all([IndicatorEvent(**row) for row in fresh])
        await db.commit()
    return len(fresh)


async def load_pending_notifications(
    db: AsyncSession,
    *,
    symbol: str,
    timeframe: str,
    since: int,
) -> list[IndicatorEvent]:
    """待推送事件：窗口内 ``notified_at`` 为空的（含本次新插入与历史投递失败的）。"""
    rows = await db.execute(
        select(IndicatorEvent)
        .where(
            IndicatorEvent.symbol == symbol.upper(),
            IndicatorEvent.timeframe == timeframe,
            IndicatorEvent.confirmed_at >= since,
            IndicatorEvent.notified_at.is_(None),
        )
        .order_by(IndicatorEvent.confirmed_at)
    )
    return list(rows.scalars().all())


async def mark_notified(db: AsyncSession, uids: Sequence[str], *, when: int | None = None) -> int:
    """投递成功后记账；失败的保持 NULL，下一次扫描自动重试。"""
    if not uids:
        return 0
    result = await db.execute(
        update(IndicatorEvent)
        .where(IndicatorEvent.uid.in_(list(uids)))
        .values(notified_at=when or int(time.time()))
    )
    await db.commit()
    return int(result.rowcount or 0)


async def load_events(
    db: AsyncSession,
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    indicator_id: str | None = None,
    start: int | None = None,
    end: int | None = None,
    confirmed_before: int | None = None,
    limit: int | None = None,
    ascending: bool = True,
) -> list[IndicatorEvent]:
    stmt = select(IndicatorEvent)
    if symbol:
        stmt = stmt.where(IndicatorEvent.symbol == symbol.upper())
    if timeframe:
        stmt = stmt.where(IndicatorEvent.timeframe == timeframe)
    if indicator_id:
        stmt = stmt.where(IndicatorEvent.indicator_id == indicator_id)
    if start is not None:
        stmt = stmt.where(IndicatorEvent.bar_time >= start)
    if end is not None:
        stmt = stmt.where(IndicatorEvent.bar_time <= end)
    if confirmed_before is not None:
        stmt = stmt.where(IndicatorEvent.confirmed_at <= confirmed_before)
    stmt = stmt.order_by(
        IndicatorEvent.confirmed_at.asc() if ascending else IndicatorEvent.confirmed_at.desc()
    )
    if limit:
        stmt = stmt.limit(limit)
    return list((await db.execute(stmt)).scalars().all())


def event_to_dict(row: IndicatorEvent) -> dict[str, Any]:
    return {
        "uid": row.uid,
        "indicator_id": row.indicator_id,
        "symbol": row.symbol,
        "timeframe": row.timeframe,
        "direction": row.direction,
        "bar_time": row.bar_time,
        "confirmed_at": row.confirmed_at,
        "first_seen_at": row.first_seen_at or row.confirmed_at,
        "price": row.price,
        "strength": row.strength,
        "state": row.state,
        "event_key": row.event_key,
        "params_hash": row.params_hash,
        "schema_version": row.schema_version,
        "evidence": row.evidence_json or {},
    }


async def upsert_outcomes(
    db: AsyncSession,
    outcomes: Sequence[Outcome],
    *,
    horizon: int = 20,
    target_r: float = 2.0,
    stop_lookback: int = 20,
) -> int:
    if not outcomes:
        return 0
    uids = [outcome.event_uid for outcome in outcomes]
    event_ids: dict[str, int] = {}
    for chunk_start in range(0, len(uids), 500):
        chunk = uids[chunk_start : chunk_start + 500]
        result = await db.execute(
            select(IndicatorEvent.uid, IndicatorEvent.id).where(IndicatorEvent.uid.in_(chunk))
        )
        event_ids.update({uid: event_id for uid, event_id in result.all()})

    existing: set[tuple[int, int, float, int]] = set()
    event_id_list = [event_ids[uid] for uid in uids if uid in event_ids]
    if event_id_list:
        result = await db.execute(
            select(
                IndicatorOutcome.event_id,
                IndicatorOutcome.horizon,
                IndicatorOutcome.target_r,
                IndicatorOutcome.stop_lookback,
            ).where(IndicatorOutcome.event_id.in_(event_id_list))
        )
        existing = {tuple(row) for row in result.all()}

    fresh: list[IndicatorOutcome] = []
    for outcome in outcomes:
        event_id = event_ids.get(outcome.event_uid)
        if event_id is None:
            continue
        key = (event_id, horizon, target_r, stop_lookback)
        if key in existing:
            continue
        existing.add(key)
        fresh.append(
            IndicatorOutcome(
                event_id=event_id,
                horizon=horizon,
                target_r=target_r,
                stop_lookback=stop_lookback,
                entry_time=outcome.entry_time,
                entry_price=outcome.entry_price,
                stop_price=outcome.stop_price,
                target_price=outcome.target_price,
                exit_time=outcome.exit_time,
                exit_price=outcome.exit_price,
                exit_reason=outcome.exit_reason,
                r_multiple=outcome.r_multiple,
                mfe_r=outcome.mfe_r,
                mae_r=outcome.mae_r,
                bars_held=outcome.bars_held,
                censored=outcome.censored,
            )
        )
    if fresh:
        db.add_all(fresh)
        await db.commit()
    return len(fresh)


async def load_outcomes(
    db: AsyncSession,
    *,
    indicator_id: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
    horizon: int = 20,
    target_r: float = 2.0,
    stop_lookback: int = 20,
) -> list[dict[str, Any]]:
    stmt = (
        select(IndicatorOutcome, IndicatorEvent)
        .join(IndicatorEvent, IndicatorOutcome.event_id == IndicatorEvent.id)
        .where(
            IndicatorOutcome.horizon == horizon,
            IndicatorOutcome.target_r == target_r,
            IndicatorOutcome.stop_lookback == stop_lookback,
        )
    )
    if indicator_id:
        stmt = stmt.where(IndicatorEvent.indicator_id == indicator_id)
    if symbol:
        stmt = stmt.where(IndicatorEvent.symbol == symbol.upper())
    if timeframe:
        stmt = stmt.where(IndicatorEvent.timeframe == timeframe)
    stmt = stmt.order_by(IndicatorOutcome.entry_time)
    rows = (await db.execute(stmt)).all()
    return [
        {
            "uid": event.uid,
            "indicator_id": event.indicator_id,
            "symbol": event.symbol,
            "timeframe": event.timeframe,
            "direction": event.direction,
            "entry_time": outcome.entry_time,
            "entry_price": outcome.entry_price,
            "stop_price": outcome.stop_price,
            "target_price": outcome.target_price,
            "exit_time": outcome.exit_time,
            "exit_price": outcome.exit_price,
            "exit_reason": outcome.exit_reason,
            "r_multiple": outcome.r_multiple,
            "mfe_r": outcome.mfe_r,
            "mae_r": outcome.mae_r,
            "bars_held": outcome.bars_held,
            "censored": outcome.censored,
        }
        for outcome, event in rows
    ]


async def outcomes_by_uid(
    db: AsyncSession,
    uids: Sequence[str],
    *,
    horizon: int = 20,
    target_r: float = 2.0,
    stop_lookback: int = 20,
) -> dict[str, dict[str, Any]]:
    if not uids:
        return {}
    rows = (
        await db.execute(
            select(IndicatorOutcome, IndicatorEvent.uid)
            .join(IndicatorEvent, IndicatorOutcome.event_id == IndicatorEvent.id)
            .where(
                IndicatorEvent.uid.in_(list(uids)),
                IndicatorOutcome.horizon == horizon,
                IndicatorOutcome.target_r == target_r,
                IndicatorOutcome.stop_lookback == stop_lookback,
            )
        )
    ).all()
    return {
        uid: {
            "exit_reason": outcome.exit_reason,
            "r_multiple": outcome.r_multiple,
            "mfe_r": outcome.mfe_r,
            "mae_r": outcome.mae_r,
            "bars_held": outcome.bars_held,
            "censored": outcome.censored,
            "won": outcome.exit_reason == "target",
        }
        for outcome, uid in rows
    }


async def backfill(
    db: AsyncSession,
    symbol: str,
    timeframe: str,
    *,
    indicator_ids: Iterable[str] | None = None,
    params: dict[str, dict[str, Any]] | None = None,
    label: bool = True,
    horizon: int = 20,
    target_r: float = 2.0,
    stop_lookback: int = 20,
) -> dict[str, Any]:
    """一个品种/周期：算事件 → 入库 → 标注 → 入库。"""
    bars = list(read_local_bars(symbol, timeframe))
    if not bars:
        return {"symbol": symbol, "timeframe": timeframe, "bars": 0, "events": 0, "inserted": 0, "outcomes": 0}
    events = build_events(symbol, timeframe, bars, indicator_ids, params)
    inserted = await upsert_events(db, events)
    outcomes_inserted = 0
    if label:
        outcomes = label_events(
            events,
            bars,
            horizon=horizon,
            target_r=target_r,
            stop_lookback=stop_lookback,
        )
        outcomes_inserted = await upsert_outcomes(
            db, outcomes, horizon=horizon, target_r=target_r, stop_lookback=stop_lookback
        )
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "bars": len(bars),
        "events": len(events),
        "inserted": inserted,
        "outcomes": outcomes_inserted,
    }
