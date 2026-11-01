"""指标告警投递幂等与重试：429/超时后不丢事件，成功前不重复发已送达的。"""
from __future__ import annotations

import asyncio
import json
import time

import pytest
from sqlalchemy import select

from app.core import indicator_alert_service as scan_service
from app.indicator_core import Event
from app.models.models import (
    Indicator,
    IndicatorEvent,
    IndicatorVersion,
    MonitorPlan,
    NotificationChannel,
    User,
    Watchlist,
)
from tests._db import make_engine, make_session, prepare

TABLES = (
    "users",
    "watchlists",
    "monitor_plans",
    "notification_channels",
    "indicators",
    "indicator_versions",
    "indicator_events",
    "indicator_outcomes",
)


class FakeRegistry:
    names = ["chanlun.buy3"]

    def run_many(self, wanted, bars, symbol, timeframe, params):
        now = int(time.time())
        return [
            Event(
                indicator_id="chanlun.buy3",
                symbol=symbol,
                timeframe=timeframe,
                bar_time=now,
                confirmed_at=now,
                direction="long",
                price=100.0,
                params_hash="deadbeef",
            )
        ]


def _bars() -> list[dict]:
    return [
        {"time": 1_700_000_000 + i * 3600, "open": 100, "high": 101, "low": 99, "close": 100}
        for i in range(80)
    ]


def _patch(monkeypatch, sender) -> None:
    monkeypatch.setattr(scan_service, "get_registry", lambda: FakeRegistry())
    monkeypatch.setattr(scan_service, "read_local_bars", lambda symbol, tf: _bars())
    monkeypatch.setattr(scan_service, "send_indicator_digest_all", sender)


async def _notified_flags(db) -> list[int | None]:
    rows = await db.execute(select(IndicatorEvent.notified_at))
    return [row[0] for row in rows.all()]


def test_failed_delivery_is_retried_then_marked(monkeypatch) -> None:
    calls: list[str] = []

    async def flaky(channels, **kwargs):
        calls.append(kwargs["symbol"])
        return len(calls) > 1  # 第一次模拟 429，第二次成功

    _patch(monkeypatch, flaky)

    async def run() -> None:
        engine = make_engine()
        await prepare(engine, TABLES)
        async with make_session(engine) as db:
            first = await scan_service.scan_and_notify(
                db, ["BTC-USD"], ["4h"], lookback_hours=1, channels=[object()]
            )
            assert first["inserted"] == 1
            assert first["pending"] == 1
            assert first["sent"] is False
            assert first["notified"] == 0
            assert await _notified_flags(db) == [None], "失败必须留待重试"

            second = await scan_service.scan_and_notify(
                db, ["BTC-USD"], ["4h"], lookback_hours=1, channels=[object()]
            )
            assert second["inserted"] == 0, "同一 uid 不重复入库"
            assert second["pending"] == 1, "失败的事件应再次进入待推送"
            assert second["sent"] is True and second["notified"] == 1
            flags = await _notified_flags(db)
            assert flags and flags[0] is not None

            third = await scan_service.scan_and_notify(
                db, ["BTC-USD"], ["4h"], lookback_hours=1, channels=[object()]
            )
            assert third["pending"] == 0 and third["sent"] is False
            assert len(calls) == 2, "已送达的事件不再重复推送"
        await engine.dispose()

    asyncio.run(run())


def test_timeout_exception_is_treated_as_failure(monkeypatch) -> None:
    calls: list[int] = []

    async def timeout_then_ok(channels, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("simulated feishu timeout")
        return True

    _patch(monkeypatch, timeout_then_ok)

    async def run() -> None:
        engine = make_engine()
        await prepare(engine, TABLES)
        async with make_session(engine) as db:
            first = await scan_service.scan_and_notify(
                db, ["BTC-USD"], ["4h"], lookback_hours=1, channels=[object()]
            )
            assert first["sent"] is False and first["notified"] == 0
            assert await _notified_flags(db) == [None]

            second = await scan_service.scan_and_notify(
                db, ["BTC-USD"], ["4h"], lookback_hours=1, channels=[object()]
            )
            assert second["sent"] is True and second["notified"] == 1
            assert len(calls) == 2
        await engine.dispose()

    asyncio.run(run())


async def _seed_plan(db):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(User(id=1, username="default", password_hash="x"))
    await db.flush()
    db.add(Watchlist(user_id=1, symbol="BTC-USD", name="BTC", asset_type="crypto"))
    indicator = Indicator(name="buy3-test", primitive_id="chanlun.buy3", status="promoted")
    db.add(indicator)
    await db.flush()
    version = IndicatorVersion(
        indicator_id=indicator.id,
        version_no=1,
        spec_json={"params": {"lookback": 20}},
        params_hash="deadbeef",
        engine_version="ev1",
        promoted_at=now,
    )
    db.add(version)
    await db.flush()
    channel = NotificationChannel(user_id=1, name="test-feishu", kind="feishu", config="{}")
    db.add(channel)
    await db.flush()
    plan = MonitorPlan(
        user_id=1,
        name="订阅计划",
        target_scope="watchlist",
        timeframe="4h",
        timeframes=json.dumps(["4h"]),
        strategies=json.dumps(["ma"]),
        parameters="{}",
        channel_id=channel.id,
        indicator_version_ids=json.dumps([version.id]),
    )
    db.add(plan)
    await db.commit()
    return plan


def test_plan_delivery_failure_keeps_cursor_and_does_not_leak_to_global(monkeypatch) -> None:
    calls: list[str] = []

    async def flaky(channels, **kwargs):
        calls.append("send")
        return len(calls) > 1  # 第一次 429，第二次成功

    _patch(monkeypatch, flaky)

    async def run() -> None:
        engine = make_engine()
        await prepare(engine, TABLES)
        async with make_session(engine) as db:
            plan = await _seed_plan(db)
            first = await scan_service.scan_plan_subscriptions(db, lookback_hours=1)
            assert first["sent"] is False
            refreshed = await db.get(MonitorPlan, plan.id)
            assert refreshed.indicator_cursor is None, "投递失败不得推进游标"
            assert await _notified_flags(db) == [None]

            second = await scan_service.scan_plan_subscriptions(db, lookback_hours=1)
            assert second["sent"] is True and second["events"] == 1
            refreshed = await db.get(MonitorPlan, plan.id)
            assert refreshed.indicator_cursor is not None
            flags = await _notified_flags(db)
            assert flags and flags[0] is not None

            # 计划已成功推送的事件，不应再被全局兜底扫描重复推送
            global_scan = await scan_service.scan_and_notify(
                db, ["BTC-USD"], ["4h"], lookback_hours=1, channels=[object()]
            )
            assert global_scan["pending"] == 0 and global_scan["sent"] is False
            assert len(calls) == 2
        await engine.dispose()

    asyncio.run(run())
