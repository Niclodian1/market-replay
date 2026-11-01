"""Tests for the 缠论 scan job: dedupe, grouping, and delivery state."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from app.core import chanlun_service
from app.core.config import settings


class FakeSession:
    def __init__(self, channels=None, cursor=0):
        self.commits = 0
        self.channels = channels or []
        self.cursor = cursor

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass

    async def execute(self, *_args, **_kwargs):
        return SimpleNamespace(
            scalar_one_or_none=lambda: None,
            scalar=lambda: self.cursor,
            scalars=lambda: SimpleNamespace(all=lambda: list(self.channels)),
        )


def _sig(tf: str, k: str, t: int, *, state: str | None = "confirmed",
         direction: str = "long") -> dict:
    kind = {"背驰买": "beichi_buy", "背驰卖": "beichi_sell"}.get(k, "buy")
    return {
        "symbol": "BTC-USD", "timeframe": tf, "mode": "seg", "kind": kind,
        "k": k, "direction": direction, "state": state, "tier": "明显",
        "t": t, "confirmedT": t, "price": 100.0, "why": "C段背驰",
        "sub": "次级别无同向趋势背驰 ✗", "upper": "—",
        "ratio": None, "trigger": None, "alertT": None,
    }


def test_dedupe_key_excludes_state():
    sig = _sig("1day", "一买", 1_700_000_000, state="confirmed")
    same = _sig("1day", "一买", 1_700_000_000, state="forming")
    assert chanlun_service._dedupe_key(sig) == "BTC-USD:1day:seg:一买:1700000000"
    assert chanlun_service._dedupe_key(same) == chanlun_service._dedupe_key(sig)


def test_signal_line_marks_meaning_and_invalid_reference():
    confirmed = _sig("1day", "一买", 1_700_000_000, state="confirmed")
    confirmed["confirmedT"] = 1_700_000_000 + 86_400
    line = chanlun_service._signal_line(confirmed)
    assert "一买" in line
    assert "下跌趋势背驰" in line
    assert "极值" in line and "确认" in line
    assert "失效参考" in line

    beichi = _sig("1day", "背驰买", 1_700_000_000, state="confirmed", direction="long")
    beichi["ratio"] = 0.42
    beichi["alertT"] = 1_700_000_000
    beichi["confirmedT"] = 1_700_000_000
    line = chanlun_service._signal_line(beichi)
    assert "动能衰竭" in line
    assert "0.42" in line


def test_scan_symbol_groups_by_timeframe_and_dedupes(monkeypatch):
    now = int(time.time())
    sig1 = _sig("1day", "一买", now, state="confirmed")
    sig1["confirmedT"] = now
    dup = _sig("1day", "一买", now, state="confirmed")  # same key (state excluded)
    dup["confirmedT"] = now
    sig2 = _sig("4h", "背驰卖", now, state="forming", direction="short")
    sig2["confirmedT"] = now

    stored: set[str] = set()
    delivered: list[tuple[str, list[dict]]] = []

    async def fake_fetch(_symbol, _tf):
        return [{
            "time": now - i * 86_400, "open": 100.0, "high": 101.0,
            "low": 99.0, "close": 100.0, "volume": 10.0,
        } for i in range(200)]

    def fake_analyze(_candles_by_tf):
        return {"1D": {}, "4H": {}, "1H": {}}

    def fake_extract(_symbol, _levels):
        return [sig1, dup, sig2]

    async def fake_store(_db, sig):
        key = chanlun_service._dedupe_key(sig)
        if key in stored:
            return None
        stored.add(key)
        return SimpleNamespace(status="pending", attempts=0, next_retry_at=None,
                               sent_at=None, last_error=None)

    async def fake_deliver(_db, signals, events, _title, _description, _color, **_kwargs):
        delivered.append((signals[0]["timeframe"], signals))

    monkeypatch.setattr(chanlun_service, "fetch_closed", fake_fetch)
    monkeypatch.setattr(chanlun_service, "analyze_symbol", fake_analyze)
    monkeypatch.setattr(chanlun_service, "extract_signals", fake_extract)
    monkeypatch.setattr(chanlun_service, "_store_signal", fake_store)
    monkeypatch.setattr(chanlun_service, "_deliver_message", fake_deliver)

    session = FakeSession(cursor=0)
    result = asyncio.run(chanlun_service.scan_symbol(session, "BTC-USD"))
    assert result["created"] == 2  # sig1 + sig2；dup 被去重
    assert {tf for tf, _ in delivered} == {"1day", "4h"}
    assert session.commits >= 1


def test_scan_symbol_only_pushes_newly_confirmed_signals(monkeypatch):
    now = int(time.time())
    old = _sig("1day", "一买", now - 5 * 86_400, state="confirmed")
    old["confirmedT"] = now - 5 * 86_400  # 已推送过（早于游标）
    new = _sig("1day", "二买", now, state="confirmed")
    new["confirmedT"] = now  # 新确认

    async def fake_fetch(_symbol, _tf):
        return [{
            "time": now - i * 86_400, "open": 100.0, "high": 101.0,
            "low": 99.0, "close": 100.0, "volume": 10.0,
        } for i in range(200)]

    monkeypatch.setattr(chanlun_service, "fetch_closed", fake_fetch)
    monkeypatch.setattr(chanlun_service, "analyze_symbol", lambda _c: {"1D": {}, "4H": {}, "1H": {}})
    monkeypatch.setattr(chanlun_service, "extract_signals", lambda _s, _l: [old, new])
    created: list[dict] = []

    async def fake_store(_db, sig):
        created.append(sig)
        return SimpleNamespace(status="pending", attempts=0, next_retry_at=None,
                               sent_at=None, last_error=None)

    monkeypatch.setattr(chanlun_service, "_store_signal", fake_store)
    monkeypatch.setattr(chanlun_service, "_deliver_message", lambda *_a, **_k: asyncio.sleep(0))

    session = FakeSession(cursor=now - 86_400)  # 游标早于 new、晚于 old
    result = asyncio.run(chanlun_service.scan_symbol(session, "BTC-USD"))
    assert result["created"] == 1
    assert [s["k"] for s in created] == ["二买"]  # 旧信号被游标过滤


def test_deliver_without_webhook_marks_in_app_only(monkeypatch):
    monkeypatch.setattr(settings, "DISCORD_WEBHOOK_URL", None)
    events = [SimpleNamespace(status="pending", attempts=0, next_retry_at=None,
                              sent_at=None, last_error=None)]
    signals = [_sig("1day", "一买", 1_700_000_000)]
    session = FakeSession()
    asyncio.run(chanlun_service._deliver_message(
        session, signals, events, "title", "description", 0x22C55E
    ))
    assert events[0].status == "in_app_only"
    assert events[0].attempts == 0
    assert session.commits >= 1


def test_failed_delivery_is_scheduled_for_retry(monkeypatch):
    monkeypatch.setattr(settings, "DISCORD_WEBHOOK_URL", "https://example.invalid/webhook")

    async def fail(*_args, **_kwargs):
        return False

    monkeypatch.setattr("app.core.chanlun_service.send_discord_alert", fail)
    events = [SimpleNamespace(status="pending", attempts=0, next_retry_at=None,
                              sent_at=None, last_error=None)]
    signals = [_sig("1day", "一买", 1_700_000_000)]
    session = FakeSession()
    asyncio.run(chanlun_service._deliver_message(
        session, signals, events, "title", "description", 0x22C55E
    ))
    assert events[0].status == "failed"
    assert events[0].attempts == 1
    assert events[0].next_retry_at is not None
    assert "重试" in events[0].last_error


def test_deliver_splits_long_description_across_multiple_messages(monkeypatch):
    monkeypatch.setattr(settings, "DISCORD_WEBHOOK_URL", "https://example.com/webhook")
    description = "缠论信号详情。\n" * 500  # 4000 chars > Discord 4096 上限
    events = [SimpleNamespace(status="pending", attempts=0, next_retry_at=None,
                              sent_at=None, last_error=None)]
    signals = [_sig("1day", "一买", 1_700_000_000)]
    calls = []

    async def fake_send(_url, title, chunk, **_kwargs):
        calls.append((title, chunk))
        return True

    monkeypatch.setattr("app.core.chanlun_service.send_discord_alert", fake_send)
    session = FakeSession()
    asyncio.run(chanlun_service._deliver_message(
        session, signals, events, "🟢 缠论信号｜BTC-USD｜1day", description, 0x22C55E
    ))
    assert len(calls) > 1
    assert all(len(chunk) <= 3900 for _title, chunk in calls)
    assert "".join(chunk for _title, chunk in calls) == description
    assert calls[0][0].endswith("（1/%d）" % len(calls))
    assert events[0].status == "sent"
    assert events[0].attempts == 1
    assert events[0].sent_at is not None
    assert events[0].last_error is None


def test_batch_color_reflects_direction():
    long_only = [_sig("1day", "一买", 1), _sig("1day", "二买", 2)]
    assert chanlun_service._batch_color(long_only) == ("🟢", 0x22C55E)
    short_only = [_sig("1day", "一卖", 1, direction="short")]
    assert chanlun_service._batch_color(short_only) == ("🔴", 0xEF4444)
    mixed = [_sig("1day", "一买", 1), _sig("1day", "二卖", 2, direction="short")]
    assert chanlun_service._batch_color(mixed)[0] == "🟠"


def test_resolve_default_channels_returns_all_active_kinds(monkeypatch):
    """缠论默认渠道应包含所有活跃渠道（Discord + 飞书），不再限定单一。"""
    feishu = SimpleNamespace(id=3, kind="feishu", user_id=1, is_default=True, is_active=True)
    discord = SimpleNamespace(id=1, kind="discord", user_id=1, is_default=False, is_active=True)
    session = FakeSession(channels=[feishu, discord])

    got = asyncio.run(chanlun_service._resolve_default_channels(session))
    assert [c.id for c in got] == [3, 1]


def test_deliver_message_pushes_to_all_channels(monkeypatch):
    """缠论告警应同时推送到所有活跃渠道。"""
    feishu = SimpleNamespace(id=3, kind="feishu", user_id=1, is_default=True, is_active=True)
    discord = SimpleNamespace(id=1, kind="discord", user_id=1, is_default=False, is_active=True)
    events = [SimpleNamespace(status="pending", attempts=0, next_retry_at=None,
                              sent_at=None, last_error=None)]
    signals = [_sig("1day", "一买", 1_700_000_000)]
    session = FakeSession(channels=[feishu, discord])
    sent_to: list[str] = []

    async def fake_send_all(channels, title, description, **_kwargs):
        sent_to.extend(c.kind for c in channels)
        return True

    monkeypatch.setattr("app.core.chanlun_service.send_to_all_channels", fake_send_all)
    asyncio.run(chanlun_service._deliver_message(
        session, signals, events, "title", "description", 0x22C55E
    ))
    assert sent_to == ["feishu", "discord"]
    assert events[0].status == "sent"
    assert events[0].last_error is None


def _candles(n: int = 60, start: int = 1_700_000_000) -> list[dict]:
    return [{
        "time": start + i * 3_600,
        "open": 100.0 + i * 0.1,
        "high": 102.0 + i * 0.1,
        "low": 99.0 + i * 0.1,
        "close": 101.0 + i * 0.1,
        "volume": 100.0,
    } for i in range(n)]


def test_chart_payloads_generates_png_per_timeframe(monkeypatch):
    candles = _candles()
    levels = {
        "1D": {"buyPointsSegmentRaw": [{"t": candles[10]["time"], "p": candles[10]["close"],
                                        "k": "一买"}],
               "sellPointsSegmentRaw": [], "buyPointsStrokeRaw": [], "sellPointsStrokeRaw": []},
        "4H": {"buyPointsSegmentRaw": [], "sellPointsSegmentRaw": [],
               "buyPointsStrokeRaw": [], "sellPointsStrokeRaw": []},
        "1H": {"buyPointsSegmentRaw": [], "sellPointsSegmentRaw": [],
               "buyPointsStrokeRaw": [], "sellPointsStrokeRaw": []},
    }
    candles_by_tf = {"1day": candles, "4h": candles, "1h": candles}
    payloads = chanlun_service._chart_payloads(
        "TEST", candles_by_tf, levels, current_price=106.0
    )
    assert [p["filename"] for p in payloads] == ["TEST_1day.png", "TEST_4h.png", "TEST_1h.png"]
    assert all(p["mime"] == "image/png" for p in payloads)
    # PNG 魔数
    assert all(p["data"][:8] == b"\x89PNG\r\n\x1a\n" for p in payloads)
    assert all(len(p["data"]) > 500 for p in payloads)


def test_signal_line_includes_current_price_placeholder_in_build_message():
    items = [_sig("1day", "一买", 1_700_000_000, state="confirmed")]
    items[0]["confirmedT"] = 1_700_000_000
    title, desc, _color = chanlun_service._build_message(
        "TEST", "1day", items, current_price=101.5
    )
    assert "当前价格" in desc
    assert "101.5" in desc


def test_macd_series_length_and_shape():
    from app.core.chanlun_chart import macd_series

    closes = [float(c["close"]) for c in _candles(100)]
    dif, dea, hist = macd_series(closes)
    assert len(dif) == len(closes) == len(dea) == len(hist)
    # hist = dif - dea
    for d, s, h in zip(dif[30:], dea[30:], hist[30:]):
        assert abs(h - (d - s)) < 1e-6


def test_chart_renders_chinese_marker_labels():
    from app.core.chanlun_chart import render_chanlun_png

    candles = _candles(80)
    markers = [
        {"t": candles[20]["time"], "p": candles[20]["close"], "k": "一买", "direction": "long"},
        {"t": candles[50]["time"], "p": candles[50]["close"], "k": "三卖", "direction": "short"},
    ]
    png = render_chanlun_png("TEST", "1H", candles, markers=markers, current_price=102.0)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 5000  # 含 MACD 副图后体积明显增大


def test_chart_renders_pivot_boxes():
    from app.core.chanlun_chart import render_chanlun_png

    candles = _candles(80)
    pivots = [
        {"start": candles[20]["time"], "end": candles[45]["time"],
         "zd": 101.0, "zg": 104.0, "gg": 106.0, "dd": 99.0},
    ]
    png = render_chanlun_png("TEST", "1H", candles, pivots=pivots)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 5000


def test_chart_payloads_includes_pivots_from_levels(monkeypatch):
    candles = _candles(60)
    levels = {
        "1D": {"buyPointsSegmentRaw": [], "sellPointsSegmentRaw": [],
               "buyPointsStrokeRaw": [], "sellPointsStrokeRaw": [],
               "pivotsSegmentRaw": []},
        "4H": {"buyPointsSegmentRaw": [], "sellPointsSegmentRaw": [],
               "buyPointsStrokeRaw": [], "sellPointsStrokeRaw": [],
               "pivotsSegmentRaw": []},
        "1H": {"buyPointsSegmentRaw": [], "sellPointsSegmentRaw": [],
               "buyPointsStrokeRaw": [], "sellPointsStrokeRaw": [],
               "pivotsSegmentRaw": [[candles[10]["time"], candles[40]["time"], 100.0, 102.0, 103.0, 99.0]]},
    }
    candles_by_tf = {"1day": candles, "4h": candles, "1h": candles}
    payloads = chanlun_service._chart_payloads(
        "TEST", candles_by_tf, levels, current_price=106.0
    )
    assert len(payloads) == 3
    # 1H 有中枢 → PNG 应包含中枢填充色
    ih_png = payloads[2]["data"]
    assert ih_png[:8] == b"\x89PNG\r\n\x1a\n"
