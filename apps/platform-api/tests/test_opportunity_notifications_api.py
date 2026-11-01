"""机会通知 API：前端契约（列表/摘要/已读/重试/模拟）与「前端调不存在的接口」回归。

浏览器实测发现前端右栏调用 `/api/opportunity-notifications*` 六个接口，
后端此前未实现（每页 404）。本测试锁住契约与路由前缀。
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api.opportunity_notifications_api import (
    _ma_periods,
    _serialize,
    list_opportunity_notifications,
    mark_opportunity_notification_read,
    retry_opportunity_notification,
)
from app.db.base import Base
from app.main import app
from app.models.models import AlertEvent, MonitorPlan, User
from tests._db import make_engine, make_session

TABLES = ("users", "monitor_plans", "notification_channels", "alert_events")


async def _prepare():
    engine = make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=[Base.metadata.tables[name] for name in TABLES])
        )
    return engine, make_session(engine)


async def _seed(db) -> AlertEvent:
    db.add(User(id=1, username="smoke", password_hash="x"))
    db.add(
        MonitorPlan(id=1, user_id=1, name="默认计划", timeframe="4h", parameters='{"ma": {"periods": [20, 60]}}')
    )
    await db.commit()
    event = AlertEvent(
        id=1,  # alert_events.id 是 BigInteger，SQLite 上不自增，测试里显式给
        dedupe_key="ma-BTC-USD-4h-1",
        plan_id=1,
        symbol="BTC-USD",
        timeframe="4h",
        strategy="ma",
        event_type="ma_breakout",
        direction="long",
        severity="signal",
        signal_time=1_700_000_000,
        reference_price=34000.0,
        payload=json.dumps(
            {"summary": "BTC 突破 MA20", "parameters": {"periods": [20]}, "data_source": "binance"}
        ),
        status="failed",
        attempts=2,
        next_retry_at=datetime.now(tz=timezone.utc) - timedelta(minutes=1),
        last_error="全部通知渠道投递失败",
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


def test_ma_periods_normalizes_payload_shapes() -> None:
    assert _ma_periods({"parameters": {"periods": [20, 60]}}) == [20, 60]
    assert _ma_periods({"parameters": {"period": 20}}) == [20]
    assert _ma_periods({"parameters": {"fast": 20, "slow": 60}}) == [20, 60]
    assert _ma_periods({}) == []


def test_list_returns_frontend_contract() -> None:
    async def run():
        engine, db = await _prepare()
        try:
            await _seed(db)
            return await list_opportunity_notifications(limit=20, status_filter=None, unread_only=False, db=db)
        finally:
            await db.close()
            await engine.dispose()

    body = asyncio.run(run())
    assert body["total"] == 1 and body["unread"] == 1
    item = body["items"][0]
    assert set(item) == {
        "id", "symbol", "strategy", "timeframe", "signal_time", "parameters", "title", "description",
        "data_source", "status", "attempts", "next_retry_at", "sent_at", "last_error", "is_read",
        "simulated", "created_at", "updated_at",
    }
    assert item["symbol"] == "BTC-USD"
    assert item["title"] == "BTC 突破 MA20"
    assert json.loads(item["parameters"]) == [20]
    assert item["status"] == "failed" and item["is_read"] is False


def test_mark_read_then_retry() -> None:
    async def run():
        engine, db = await _prepare()
        try:
            event = await _seed(db)
            marked = await mark_opportunity_notification_read(event.id, db=db)
            retried = await retry_opportunity_notification(event.id, db=db)
            return marked, retried
        finally:
            await db.close()
            await engine.dispose()

    marked, retried = asyncio.run(run())
    assert marked["is_read"] is True
    # 无可用渠道时投递链路把状态置为 in_app_only，站内信号保留
    assert retried["status"] == "in_app_only"
    assert retried["next_retry_at"] is None


def test_retry_rejects_already_sent() -> None:
    async def run():
        engine, db = await _prepare()
        try:
            event = await _seed(db)
            event.status = "sent"
            await db.commit()
            with pytest.raises(HTTPException) as exc:
                await retry_opportunity_notification(event.id, db=db)
            return exc.value.status_code
        finally:
            await db.close()
            await engine.dispose()

    assert asyncio.run(run()) == 409


def test_unknown_id_is_404() -> None:
    async def run():
        engine, db = await _prepare()
        try:
            with pytest.raises(HTTPException) as exc:
                await mark_opportunity_notification_read(999_999, db=db)
            return exc.value.status_code
        finally:
            await db.close()
            await engine.dispose()

    assert asyncio.run(run()) == 404


def test_serialize_marks_simulated() -> None:
    event = AlertEvent(
        dedupe_key="sim-1", plan_id=1, symbol="BTC-USD", timeframe="4h", strategy="ma",
        event_type="ma_breakout", direction="long", severity="signal", signal_time=1,
        reference_price=1.0, payload=json.dumps({"simulated": True, "parameters": {"period": 20}}),
        status="pending", attempts=0,
    )
    item = _serialize(event)
    assert item["simulated"] is True
    assert json.loads(item["parameters"]) == [20]


def test_router_exposes_the_six_frontend_paths() -> None:
    """前端调用的路径必须真实存在（本轮修复的 404 回归）。"""
    paths = {
        (route.path, method)
        for route in app.routes
        for method in getattr(route, "methods", set())
        if "opportunity-notifications" in getattr(route, "path", "")
    }
    assert ("/api/opportunity-notifications", "GET") in paths
    assert ("/api/opportunity-notifications/summary", "GET") in paths
    assert ("/api/opportunity-notifications/scan-now", "POST") in paths
    assert ("/api/opportunity-notifications/simulate", "POST") in paths
    assert ("/api/opportunity-notifications/{notification_id}/read", "POST") in paths
    assert ("/api/opportunity-notifications/{notification_id}/retry", "POST") in paths
