"""收盘对齐扫描窗口 + 调度节流。"""
from __future__ import annotations

import asyncio

from app.core import scan_window
from app.core.scan_window import closed_bar_time, due_timeframes, timeframe_seconds
from app.tasks import alert_scheduler

FOUR_H = 14_400
# 取一个「距 4h 收盘 60 秒」的时刻，避免测试依赖真实时钟
BASE = 1_700_000_000 - (1_700_000_000 % FOUR_H) + 60


def test_timeframe_seconds_aliases() -> None:
    assert timeframe_seconds("4h") == FOUR_H
    assert timeframe_seconds("1D") == 86_400
    assert timeframe_seconds("1d") == 86_400
    assert timeframe_seconds("weird") is None


def test_closed_bar_time_aligns_to_boundary() -> None:
    close = closed_bar_time(BASE, "4h")
    assert close is not None
    assert BASE - close == 60
    assert close % FOUR_H == 0
    assert closed_bar_time(BASE, "weird") is None


def test_aligned_scan_fires_once_per_close() -> None:
    due, reason = due_timeframes(BASE, ["4h"], {}, BASE)
    assert (due, reason) == (["4h"], "aligned")

    close = closed_bar_time(BASE, "4h")
    assert close is not None
    due, reason = due_timeframes(BASE, ["4h"], {"4h": close}, BASE)
    assert (due, reason) == ([], "idle")


def test_fallback_scan_when_window_missed() -> None:
    late = BASE + 3_600  # 已错过 5 分钟对齐窗口
    due, reason = due_timeframes(late, ["4h", "1day"], {}, late - 7_200)
    assert reason == "fallback"
    assert due == ["4h", "1day"]

    due, reason = due_timeframes(late, ["4h"], {}, late - 60)
    assert (due, reason) == ([], "idle")


def test_unknown_timeframe_ignored() -> None:
    due, reason = due_timeframes(BASE, ["weird"], {}, BASE)
    assert (due, reason) == ([], "idle")


def test_indicator_scan_job_skips_when_idle_and_runs_when_aligned(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    async def fake_plan(session, **kwargs):
        calls.append(("plan", kwargs))
        return {"plans": 0}

    async def fake_scan(session, symbols, timeframes, **kwargs):
        calls.append(("scan", list(timeframes)))
        return {}

    class FakeSession:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(alert_scheduler, "AsyncSessionLocal", lambda: FakeSession())
    monkeypatch.setattr(alert_scheduler, "scan_plan_subscriptions", fake_plan)
    monkeypatch.setattr(alert_scheduler, "scan_and_notify", fake_scan)
    monkeypatch.setattr(alert_scheduler, "time", type("T", (), {"time": staticmethod(lambda: BASE)}))
    monkeypatch.setattr(alert_scheduler.settings, "INDICATOR_ALERTS_ENABLED", True)
    monkeypatch.setattr(alert_scheduler.settings, "CHANLUN_SYMBOLS", "BTC-USD")
    monkeypatch.setattr(alert_scheduler.settings, "INDICATOR_ALERT_TIMEFRAMES", "4h")

    alert_scheduler._last_scanned_close.clear()
    alert_scheduler._last_indicator_fallback_ts = BASE
    close = closed_bar_time(BASE, "4h")
    assert close is not None
    alert_scheduler._last_scanned_close["4h"] = close
    asyncio.run(alert_scheduler._indicator_scan_job())
    assert calls == [], "同一根收盘 bar 不应重复扫描"

    alert_scheduler._last_scanned_close.clear()
    asyncio.run(alert_scheduler._indicator_scan_job())
    assert [name for name, _ in calls] == ["plan", "scan"]
    assert calls[1][1] == ["4h"]
    assert alert_scheduler._last_scanned_close["4h"] == close
