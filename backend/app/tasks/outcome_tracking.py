from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.edge_service import upsert_edge_stat
from app.models.models import MarketCandle, SignalEvent


@dataclass(frozen=True)
class OutcomeResult:
    outcome: str
    realized_r: float
    closed_at_ts: int


def _judge_outcome(event: SignalEvent, candles: list[MarketCandle], horizon: int) -> OutcomeResult | None:
    if not candles:
        return None
    entry = float(event.fired_price)
    risk = max(entry * 0.015, 1e-9)
    target_r = 2.0
    if event.direction == "short":
        stop = entry + risk
        target = entry - risk * target_r
    else:
        stop = entry - risk
        target = entry + risk * target_r

    for candle in candles[:horizon]:
        high = float(candle.high)
        low = float(candle.low)
        if event.direction == "short":
            if high >= stop:
                return OutcomeResult("loss", -1.0, candle.time)
            if low <= target:
                return OutcomeResult("win", target_r, candle.time)
        else:
            if low <= stop:
                return OutcomeResult("loss", -1.0, candle.time)
            if high >= target:
                return OutcomeResult("win", target_r, candle.time)

    if len(candles) < horizon:
        return None
    last = candles[horizon - 1]
    raw_r = (float(last.close) - entry) / risk
    if event.direction == "short":
        raw_r = -raw_r
    return OutcomeResult("timeout", max(min(raw_r, target_r), -1.0), last.time)


async def _future_candles(db: AsyncSession, event: SignalEvent, horizon: int) -> list[MarketCandle]:
    result = await db.execute(
        select(MarketCandle)
        .where(
            MarketCandle.symbol == event.symbol,
            MarketCandle.timeframe == event.timeframe,
            MarketCandle.time > event.fired_at_ts,
        )
        .order_by(MarketCandle.time)
        .limit(horizon)
    )
    return list(result.scalars().all())


async def run_outcome_backfill(
    db: AsyncSession,
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    horizon: int = 12,
    limit: int = 500,
) -> dict[str, int]:
    stmt = select(SignalEvent).where(SignalEvent.outcome.is_(None)).order_by(SignalEvent.fired_at_ts).limit(limit)
    if symbol:
        stmt = stmt.where(SignalEvent.symbol == symbol.upper())
    if timeframe:
        stmt = stmt.where(SignalEvent.timeframe == timeframe)

    result = await db.execute(stmt)
    events = list(result.scalars().all())
    closed = 0
    for event in events:
        candles = await _future_candles(db, event, horizon)
        outcome = _judge_outcome(event, candles, horizon)
        if outcome is None:
            continue
        event.outcome = outcome.outcome
        event.realized_r = outcome.realized_r
        event.closed_at_ts = outcome.closed_at_ts
        closed += 1
    if closed:
        await db.commit()

    edge_upserts = await refresh_single_signal_edges(db, symbol=symbol, timeframe=timeframe)
    return {"processed": len(events), "closed": closed, "edge_upserts": edge_upserts}


async def refresh_single_signal_edges(
    db: AsyncSession,
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
) -> int:
    stmt = select(SignalEvent).where(SignalEvent.outcome.is_not(None))
    if symbol:
        stmt = stmt.where(SignalEvent.symbol == symbol.upper())
    if timeframe:
        stmt = stmt.where(SignalEvent.timeframe == timeframe)
    result = await db.execute(stmt)
    events = list(result.scalars().all())

    grouped: dict[tuple[str, str, str, str], list[SignalEvent]] = defaultdict(list)
    for event in events:
        grouped[(event.symbol, event.timeframe, event.signal_name, event.direction)].append(event)

    upserts = 0
    for (group_symbol, group_tf, signal_name, direction), rows in grouped.items():
        sample_size = len(rows)
        if sample_size < 3:
            continue
        wins = sum(1 for row in rows if (row.realized_r or 0) > 0)
        avg_r = sum(float(row.realized_r or 0) for row in rows) / sample_size
        losses = [abs(float(row.realized_r or 0)) for row in rows if (row.realized_r or 0) <= 0]
        avg_loss = sum(losses) / len(losses) if losses else 1.0
        win_rate = wins / sample_size
        expectancy = avg_r * win_rate - avg_loss * (1 - win_rate)
        await upsert_edge_stat(
            db,
            symbol=group_symbol,
            timeframe=group_tf,
            signals=[signal_name],
            direction=direction,
            sample_size=sample_size,
            wins=wins,
            avg_r=avg_r,
            expectancy=expectancy,
            window_start_ts=min(row.fired_at_ts for row in rows),
            window_end_ts=max(row.closed_at_ts or row.fired_at_ts for row in rows),
        )
        upserts += 1
    return upserts
