"""指标告警扫描：只推「库中原本没有」的事件（重启不重复轰炸）。"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.core import indicator_alert_service as scan_service
from app.indicator_core import Event
from tests._db import make_engine, make_session, prepare

TABLES = ("indicator_events", "indicator_outcomes")


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


def test_scan_sends_only_new_events(monkeypatch) -> None:
    sent: list[dict] = []

    async def fake_send(channels, **kwargs):
        sent.append({"channels": len(channels), **kwargs})
        return True

    monkeypatch.setattr(scan_service, "get_registry", lambda: FakeRegistry())
    monkeypatch.setattr(scan_service, "read_local_bars", lambda symbol, tf: _bars())
    monkeypatch.setattr(scan_service, "send_indicator_digest_all", fake_send)

    async def run() -> None:
        engine = make_engine()
        await prepare(engine, TABLES)
        async with make_session(engine) as db:
            first = await scan_service.scan_and_notify(
                db, ["BTC-USD"], ["4h"], lookback_hours=1, channels=[object()]
            )
            assert first["pending"] == 1 and first["inserted"] == 1 and first["sent"] is True
            assert len(sent) == 1 and sent[0]["symbol"] == "BTC-USD"

            second = await scan_service.scan_and_notify(
                db, ["BTC-USD"], ["4h"], lookback_hours=1, channels=[object()]
            )
            assert second["pending"] == 0 and second["inserted"] == 0
            assert second["sent"] is False
            assert len(sent) == 1
        await engine.dispose()

    asyncio.run(run())


def test_scan_without_channels_does_not_send(monkeypatch) -> None:
    async def boom(*args, **kwargs):
        raise AssertionError("没有渠道时不应调用发送")

    monkeypatch.setattr(scan_service, "get_registry", lambda: FakeRegistry())
    monkeypatch.setattr(scan_service, "read_local_bars", lambda symbol, tf: _bars())
    monkeypatch.setattr(scan_service, "send_indicator_digest_all", boom)

    async def run() -> None:
        engine = make_engine()
        await prepare(engine, TABLES)
        async with make_session(engine) as db:
            result = await scan_service.scan_and_notify(
                db, ["BTC-USD"], ["4h"], lookback_hours=1, channels=[]
            )
            assert result["pending"] == 1
            assert result["sent"] is False
        await engine.dispose()

    asyncio.run(run())
