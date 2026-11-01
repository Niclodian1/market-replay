"""事件库/结果库的集成测试（异步 SQLite，不依赖 Postgres）。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core import event_store
from app.indicator_core import Event
from app.indicator_core.outcomes import Outcome
from tests._db import make_engine, make_session, prepare

PARQUET_1D = Path(__file__).resolve().parents[3] / "data" / "crypto" / "BTC_USD_1day.parquet"
_TABLES = ("indicator_events", "indicator_outcomes")


def _event(index: int, *, indicator_id: str = "ma.breakout") -> Event:
    return Event(
        indicator_id=indicator_id,
        symbol="BTC-USD",
        timeframe="1day",
        bar_time=1_700_000_000 + index * 86_400,
        confirmed_at=1_700_000_000 + index * 86_400,
        direction="long" if index % 2 == 0 else "short",
        price=100.0 + index,
        params_hash="abc12345",
    )


def _outcome(event: Event, r: float) -> Outcome:
    return Outcome(
        event_uid=event.uid,
        indicator_id=event.indicator_id,
        symbol=event.symbol,
        timeframe=event.timeframe,
        direction=event.direction,
        entry_time=event.confirmed_at,
        entry_price=event.price,
        stop_price=event.price - 1,
        target_price=event.price + 2,
        exit_time=event.confirmed_at + 86_400,
        exit_price=event.price + 2,
        exit_reason="target" if r > 0 else "stop",
        r_multiple=r,
        mfe_r=max(r, 0),
        mae_r=min(r, 0),
        bars_held=1,
        censored=False,
    )


def test_upsert_events_is_idempotent_and_filters() -> None:
    async def run() -> None:
        engine = make_engine()
        await prepare(engine, _TABLES)
        events = [_event(i) for i in range(3)]
        async with make_session(engine) as db:
            assert await event_store.upsert_events(db, events) == 3
            assert await event_store.upsert_events(db, events) == 0

            rows = await event_store.load_events(db, symbol="btc-usd", timeframe="1day")
            assert len(rows) == 3
            assert rows[0].confirmed_at <= rows[-1].confirmed_at

            short_only = await event_store.load_events(
                db, symbol="BTC-USD", indicator_id="ma.breakout", limit=10
            )
            assert len(short_only) == 3

            before = await event_store.load_events(
                db, symbol="BTC-USD", confirmed_before=1_700_000_000 + 86_400
            )
            assert len(before) == 2

            first = event_store.event_to_dict(rows[0])
            assert first["uid"] == events[0].uid
            assert first["evidence"] == {}
        await engine.dispose()

    asyncio.run(run())


def test_upsert_outcomes_requires_events_and_is_idempotent() -> None:
    async def run() -> None:
        engine = make_engine()
        await prepare(engine, _TABLES)
        events = [_event(i) for i in range(2)]
        outcomes = [_outcome(event, 2.0 if i == 0 else -1.0) for i, event in enumerate(events)]
        async with make_session(engine) as db:
            assert await event_store.upsert_outcomes(db, outcomes) == 0  # 事件还不存在
            await event_store.upsert_events(db, events)
            assert await event_store.upsert_outcomes(db, outcomes) == 2
            assert await event_store.upsert_outcomes(db, outcomes) == 0

            rows = await event_store.load_outcomes(db, indicator_id="ma.breakout")
            assert len(rows) == 2
            assert {row["exit_reason"] for row in rows} == {"target", "stop"}
        await engine.dispose()

    asyncio.run(run())


@pytest.mark.skipif(not PARQUET_1D.exists(), reason="BTC 日线数据不可用")
def test_backfill_real_data_then_idempotent() -> None:
    async def run() -> None:
        engine = make_engine()
        await prepare(engine, _TABLES)
        async with make_session(engine) as db:
            first = await event_store.backfill(
                db, "BTC-USD", "1day", indicator_ids=["ma.breakout"]
            )
            assert first["bars"] > 1000
            assert first["events"] > 0
            assert first["inserted"] == first["events"]
            assert first["outcomes"] > 0

            second = await event_store.backfill(
                db, "BTC-USD", "1day", indicator_ids=["ma.breakout"]
            )
            assert second["inserted"] == 0
            assert second["outcomes"] == 0

            rows = await event_store.load_outcomes(db, indicator_id="ma.breakout")
            assert len(rows) == first["outcomes"]
        await engine.dispose()

    asyncio.run(run())
