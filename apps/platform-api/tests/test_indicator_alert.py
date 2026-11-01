"""指标告警：深链与带统计的摘要（不真正发网络请求）。"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.core import indicator_alert
from app.indicator_core import Event


def _event(direction: str = "long") -> Event:
    return Event(
        indicator_id="chanlun.buy3",
        symbol="BTC-USD",
        timeframe="4h",
        bar_time=1_700_000_000,
        confirmed_at=1_700_000_000,
        direction=direction,
        price=100.0,
    )


def test_deep_link_contains_jump_target() -> None:
    link = indicator_alert.event_deep_link("https://mr.example.com/", _event())
    assert link == "https://mr.example.com/watch?symbol=BTC-USD&timeframe=4h&t=1700000000"


def test_digest_contains_stats_and_deep_link(monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def fake_send(channel, title, description, **kwargs):
        captured["title"] = title
        captured["description"] = description
        return True

    monkeypatch.setattr(indicator_alert, "send_to_channel", fake_send)
    channel = SimpleNamespace(kind="feishu", is_active=True, config="{}")
    sent = asyncio.run(
        indicator_alert.send_indicator_digest(
            channel,
            symbol="BTC-USD",
            timeframe="4h",
            events=[_event()],
            summaries={
                "chanlun.buy3": {
                    "n": 147,
                    "win_rate": 0.43,
                    "expectancy_r": 0.12,
                    "wilson_low": 0.35,
                }
            },
        )
    )
    assert sent is True
    assert "1 个指标触发" in captured["title"]
    assert "chanlun.buy3" in captured["description"]
    assert "n=147" in captured["description"]
    assert "胜率 43.0%" in captured["description"]
    assert "/watch?symbol=BTC-USD" in captured["description"]


def test_empty_events_do_not_send(monkeypatch) -> None:
    async def boom(*args, **kwargs):
        raise AssertionError("不应发送")

    monkeypatch.setattr(indicator_alert, "send_to_channel", boom)
    assert asyncio.run(
        indicator_alert.send_indicator_digest(
            SimpleNamespace(kind="feishu", is_active=True, config="{}"),
            symbol="BTC-USD",
            timeframe="4h",
            events=[],
            summaries={},
        )
    ) is False
