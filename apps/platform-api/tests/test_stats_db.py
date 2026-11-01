"""DB 版统计接口测试（异步 SQLite + 真实 BTC 日线回填）。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.api.stats_api import (
    BackfillRequest,
    backfill_events,
    list_events,
    stats_detail,
    stats_overview,
)
from tests._db import make_engine, make_session, prepare

PARQUET_1D = Path(__file__).resolve().parents[3] / "data" / "crypto" / "BTC_USD_1day.parquet"
pytestmark = pytest.mark.skipif(not PARQUET_1D.exists(), reason="BTC 日线数据不可用")


def test_backfill_then_overview_detail_and_events() -> None:
    async def run() -> None:
        engine = make_engine()
        await prepare(engine)
        async with make_session(engine) as db:
            stats = await backfill_events(
                BackfillRequest(symbol="BTC-USD", timeframe="1day", indicator_ids=["ma.breakout"]),
                db=db,
            )
            assert stats["inserted"] > 0 and stats["outcomes"] > 0

            overview = await stats_overview(db=db)
            row = next(item for item in overview["indicators"] if item["indicator_id"] == "ma.breakout")
            assert row["n"] > 0
            assert 0 <= row["win_rate"] <= 1
            assert "wilson_low" in row

            detail = await stats_detail("ma.breakout", db=db)
            assert detail["summary"]["n"] == row["n"]
            assert detail["by_year"] and detail["by_direction"]
            assert detail["outcomes"]
            first = detail["outcomes"][0]
            assert {"entry_time", "exit_reason", "r_multiple"} <= set(first)

            events = await list_events(symbol="BTC-USD", timeframe="1day", db=db)
            assert events["count"] > 0
            assert "evidence" in events["events"][0]

            cutoff = events["events"][len(events["events"]) // 2]["confirmed_at"]
            filtered = await list_events(
                symbol="BTC-USD", timeframe="1day", confirmed_before=cutoff, db=db
            )
            assert filtered["count"] < events["count"]

            again = await backfill_events(
                BackfillRequest(symbol="BTC-USD", timeframe="1day", indicator_ids=["ma.breakout"]),
                db=db,
            )
            assert again["inserted"] == 0 and again["outcomes"] == 0
        await engine.dispose()

    asyncio.run(run())
