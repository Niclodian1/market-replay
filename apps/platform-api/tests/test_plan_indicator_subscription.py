"""监控计划订阅指标版本：只推 promoted 版本、按计划游标去重。"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.monitoring_api import _validate_indicator_versions
from app.core import indicator_alert_service as scan_service
from app.indicator_core import Event
from app.models.models import (
    Indicator,
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
    "indicators",
    "indicator_versions",
    "notification_channels",
    "indicator_events",
    "indicator_outcomes",
)


class FakeRegistry:
    names = ["chanlun.buy3"]
    seen_params: list[dict] = []

    def run_many(self, wanted, bars, symbol, timeframe, params):
        self.seen_params.append(params)
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
                params_hash="feedbeef",
            )
        ]


def _bars() -> list[dict]:
    return [
        {"time": 1_700_000_000 + i * 3600, "open": 100, "high": 101, "low": 99, "close": 100}
        for i in range(80)
    ]


async def _seed(db, *, promoted: bool = True):
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
        params_hash="feedbeef",
        engine_version="ev1",
        promoted_at=now if promoted else None,
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
    return plan, version


def test_plan_scan_uses_params_and_cursor(monkeypatch) -> None:
    sent: list[dict] = []
    FakeRegistry.seen_params = []

    async def fake_send(channels, **kwargs):
        sent.append({"channel": channels[0].name, **kwargs})
        return True

    monkeypatch.setattr(scan_service, "get_registry", lambda: FakeRegistry())
    monkeypatch.setattr(scan_service, "read_local_bars", lambda symbol, tf: _bars())
    monkeypatch.setattr(scan_service, "send_indicator_digest_all", fake_send)

    async def run() -> None:
        engine = make_engine()
        await prepare(engine, TABLES)
        async with make_session(engine) as db:
            plan, _ = await _seed(db)
            first = await scan_service.scan_plan_subscriptions(db, lookback_hours=1)
            assert first["events"] == 1
            assert FakeRegistry.seen_params[0]["chanlun.buy3"] == {"lookback": 20}
            assert sent and sent[0]["channel"] == "test-feishu"
            assert sent[0]["symbol"] == "BTC-USD"

            second = await scan_service.scan_plan_subscriptions(db, lookback_hours=1)
            assert second["events"] == 0
            assert len(sent) == 1
            refreshed = await db.get(MonitorPlan, plan.id)
            assert refreshed.indicator_cursor is not None
        await engine.dispose()

    asyncio.run(run())


def test_unpromoted_version_cannot_be_subscribed() -> None:
    async def run() -> None:
        engine = make_engine()
        await prepare(engine, TABLES)
        async with make_session(engine) as db:
            _, version = await _seed(db, promoted=False)
            with pytest.raises(HTTPException) as excinfo:
                await _validate_indicator_versions(db, [version.id])
            assert excinfo.value.status_code == 400
            assert "未发布" in str(excinfo.value.detail)
        await engine.dispose()

    asyncio.run(run())


def test_promoted_versions_endpoint_lists_only_live_versions() -> None:
    from app.api.library_api import list_promoted_versions

    async def run() -> None:
        engine = make_engine()
        await prepare(engine, TABLES)
        async with make_session(engine) as db:
            plan, version = await _seed(db)
            payload = await list_promoted_versions(db)
            assert payload["count"] == 1
            row = payload["versions"][0]
            assert row["id"] == version.id
            assert row["params"] == {"lookback": 20}
            assert row["indicator_name"] == "buy3-test"

            from app.api.monitoring_api import _plan_out

            out = await _plan_out(db, plan)
            assert out.indicator_version_ids == [version.id]
        await engine.dispose()

    asyncio.run(run())
