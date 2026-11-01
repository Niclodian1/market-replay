"""V1 生产告警契约 characterization 测试（告警体系 V2 · 里程碑 0 · Task 2）。

目标：证明本次因果修复（trend_classifier / strategy_detectors /
strategy_backtest_engine）没有改变 V1 生产告警契约，锁定：
  1. monitor 的 detector 集合（ma / macd / structure，不含回测策略探测器）
  2. 事件字段结构（strategy/event_type/direction/severity/signal_time/
     reference_price/summary/payload）与 payload 可 JSON 序列化
  3. monitor 只报告最后一根已完成 bar（无回填）
  4. dedupe 语义：plan:symbol:timeframe:strategy:event_type:direction:signal_time
     唯一键 + 数据库唯一约束去重，重复事件返回 None
  5. 通知路由：计划绑定渠道 + 用户全部活跃渠道，按 id 去重，默认渠道优先
  6. 投递状态机：sent / failed(+5min 重试) / in_app_only
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

from app.core.monitor_engine import (
    _deliver_batch,
    _resolve_active_channels,
    _store_new_event,
)
from app.core.signals.monitor_signals import DETECTORS, run_monitor_detectors
from app.models.models import AlertEvent


# ── 1. detector 集合冻结 ───────────────────────────────────────────────────────

def test_monitor_detector_set_is_frozen():
    """生产 monitor 的 detector 集合必须恰好是 {ma, macd, structure}。"""
    assert set(DETECTORS) == {"ma", "macd", "structure"}


def test_strategy_detectors_not_in_monitor_path():
    """回测策略探测器（trend/momentum/reversal）不得进入生产 monitor 路径。"""
    for name in ("trend", "momentum", "reversal"):
        assert name not in DETECTORS


# ── 2. 事件字段结构 ────────────────────────────────────────────────────────────

def _candles(n: int = 130) -> list[dict]:
    out = []
    ts = 1_700_000_000
    for i in range(n):
        out.append({
            "time": ts + i * 3600,
            "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0,
            "volume": 100.0,
        })
    out[-2].update(open=100.0, high=100.2, low=98.7, close=99.0)
    out[-1].update(open=99.0, high=102.2, low=98.8, close=102.0, volume=200.0)
    return out


EVENT_FIELDS = {"strategy", "event_type", "direction", "severity",
                "signal_time", "reference_price", "summary", "payload"}


def test_event_fields_shape_and_json_serializable():
    """每个事件必须包含固定字段集，payload 必须是可 JSON 序列化的 dict。"""
    import json

    candles = _candles()
    items = run_monitor_detectors(candles, "1h", ["ma", "macd", "structure"])
    assert items, "构造序列应产生至少一个事件"
    for item in items:
        assert set(item) == EVENT_FIELDS, f"事件字段异常: {sorted(set(item) ^ EVENT_FIELDS)}"
        assert isinstance(item["payload"], dict)
        assert "evidence" in item["payload"] and "parameters" in item["payload"]
        json.dumps(item, ensure_ascii=False)  # 可序列化
        assert item["signal_time"] == int(candles[-1]["time"])


def test_monitor_only_reports_latest_closed_bar():
    """monitor 的每个 detector 都只报告最后一根已完成 bar（无回填）。"""
    candles = _candles()
    for name, detector in DETECTORS.items():
        items = detector(candles, "1h")
        for item in items:
            assert item["signal_time"] == int(candles[-1]["time"]), \
                f"{name} 回填了历史 bar"


# ── 3. dedupe 语义 ─────────────────────────────────────────────────────────────

class _Nested:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeStoreDb:
    """模拟 SQLAlchemy session：flush 时校验 dedupe_key 唯一约束。"""

    def __init__(self):
        self.flushed_keys: set[str] = set()
        self.pending: list[AlertEvent] = []
        self.events: list[AlertEvent] = []

    def begin_nested(self):
        return _Nested(self)

    def add(self, obj):
        self.pending.append(obj)

    async def flush(self):
        for event in self.pending:
            if event.dedupe_key in self.flushed_keys:
                raise IntegrityError("INSERT", {}, Exception("duplicate key"))
            self.flushed_keys.add(event.dedupe_key)
            self.events.append(event)
        self.pending = []


def _plan():
    return SimpleNamespace(id=7)


def _item(signal_time: int = 1_700_000_000 + 129 * 3600, direction: str = "long"):
    return {
        "strategy": "ma",
        "event_type": "ma_breakout",
        "direction": direction,
        "severity": "signal",
        "signal_time": signal_time,
        "reference_price": 102.0,
        "summary": "收盘上穿 MA30，实体 1.20 ATR",
        "payload": {"evidence": {}, "parameters": {"period": 30}},
    }


def test_dedupe_semantics_same_key_returns_none():
    """相同 dedupe_key（同 plan/symbol/timeframe/strategy/event_type/direction/
    signal_time）只产生一个事件，重复插入返回 None。"""
    db = _FakeStoreDb()
    first = asyncio.run(_store_new_event(db, _plan(), "BTCUSDT", "1h", _item()))
    assert first is not None
    assert first.dedupe_key == "7:BTCUSDT:1h:ma:ma_breakout:long:1700464400"
    second = asyncio.run(_store_new_event(db, _plan(), "BTCUSDT", "1h", _item()))
    assert second is None, "重复 dedupe_key 必须返回 None（数据库唯一约束去重）"


def test_dedupe_distinct_signal_time_or_direction_are_separate():
    """signal_time 或 direction 不同 → 不同 dedupe_key → 各自入库。"""
    db = _FakeStoreDb()
    a = asyncio.run(_store_new_event(db, _plan(), "BTCUSDT", "1h", _item(signal_time=111)))
    b = asyncio.run(_store_new_event(db, _plan(), "BTCUSDT", "1h", _item(signal_time=222)))
    c = asyncio.run(_store_new_event(db, _plan(), "BTCUSDT", "1h", _item(signal_time=111, direction="short")))
    assert a is not None and b is not None and c is not None
    assert len({a.dedupe_key, b.dedupe_key, c.dedupe_key}) == 3


def test_event_payload_enriched_with_strength():
    """入库事件 payload 必须包含 summary / strength / strength_label。"""
    import json

    db = _FakeStoreDb()
    event = asyncio.run(_store_new_event(db, _plan(), "BTCUSDT", "1h", _item()))
    payload = json.loads(event.payload)
    assert payload["summary"] == _item()["summary"]
    assert payload["strength"] >= 1
    assert payload["strength_label"]


# ── 4. 通知路由 ────────────────────────────────────────────────────────────────

class _FakeChannelsDb:
    """模拟 db.get / db.execute，返回预先给定的渠道表。"""

    def __init__(self, channels: dict[int, SimpleNamespace]):
        self.channels = channels
        self.user_channels = [
            c for c in channels.values()
            if getattr(c, "is_active", False)
        ]

    async def get(self, model, channel_id):
        return self.channels.get(channel_id)

    async def execute(self, stmt):
        result = SimpleNamespace()
        result.scalars = lambda: SimpleNamespace(all=lambda: list(self.user_channels))
        return result


def _channel(cid: int, *, active: bool = True, default: bool = False, user: int = 1):
    return SimpleNamespace(
        id=cid, user_id=user, is_active=active, is_default=default,
    )


def test_notification_routing_plan_channel_plus_user_active_channels():
    """路由 = 计划绑定渠道（若活跃）+ 用户全部活跃渠道，按 id 去重。"""
    channels = {
        1: _channel(1, default=True),
        2: _channel(2, active=False),          # 不活跃，排除
        3: _channel(3, user=1),
    }
    db = _FakeChannelsDb(channels)
    plan = SimpleNamespace(channel_id=3, user_id=1)
    routed = asyncio.run(_resolve_active_channels(db, plan))
    assert [c.id for c in routed] == [1, 3], "默认渠道优先 + 去重"


def test_notification_routing_inactive_bound_channel_falls_back():
    """绑定渠道不活跃 → 回退到用户活跃渠道。"""
    channels = {1: _channel(1, default=True), 2: _channel(2, active=False)}
    db = _FakeChannelsDb(channels)
    plan = SimpleNamespace(channel_id=2, user_id=1)   # 绑定渠道不活跃
    routed = asyncio.run(_resolve_active_channels(db, plan))
    assert [c.id for c in routed] == [1]


# ── 5. 投递状态机 ──────────────────────────────────────────────────────────────

def _event() -> AlertEvent:
    return AlertEvent(
        dedupe_key="k", plan_id=7, symbol="BTCUSDT", timeframe="1h",
        strategy="ma", event_type="ma_breakout", direction="long",
        severity="signal", signal_time=1_700_000_000, reference_price=102.0,
        payload="{}",
        status="pending", attempts=0, next_retry_at=None, sent_at=None,
        last_error=None,
    )


def _delivery_env():
    channels = {1: _channel(1, default=True), 3: _channel(3, user=1)}
    db = _FakeChannelsDb(channels)
    plan = SimpleNamespace(channel_id=None, user_id=1, id=7)
    return db, plan


def test_delivery_no_channels_marks_in_app_only(monkeypatch):
    """无可用渠道 → status=in_app_only，不调用发送。"""
    import app.core.monitor_engine as me

    sent = []
    monkeypatch.setattr(me, "send_to_all_channels", lambda *a, **k: sent.append(1) or True)
    db = _FakeChannelsDb({})                       # 无任何渠道
    plan = SimpleNamespace(channel_id=None, user_id=1, id=7)
    event = _event()
    asyncio.run(_deliver_batch(db, plan, "BTCUSDT", "1h", [_item()], [event]))
    assert event.status == "in_app_only"
    assert "未配置可用的通知渠道" in event.last_error
    assert sent == []


def test_delivery_success_marks_sent(monkeypatch):
    """发送成功 → status=sent、attempts=1、sent_at 设置、last_error 清空。"""
    import app.core.monitor_engine as me

    calls = {}
    async def fake_send(channels, title, description, **kwargs):
        calls["channels"] = [c.id for c in channels]
        calls["title"] = title
        calls["description"] = description
        return True

    monkeypatch.setattr(me, "send_to_all_channels", fake_send)
    db, plan = _delivery_env()
    event = _event()
    asyncio.run(_deliver_batch(db, plan, "BTCUSDT", "1h", [_item()], [event]))
    assert event.status == "sent"
    assert event.attempts == 1
    assert event.sent_at is not None
    assert event.last_error is None
    assert calls["channels"] == [1, 3], "路由到全部活跃渠道"
    assert "多头机会" in calls["title"] and "MA突破" in calls["description"]


def test_delivery_failure_schedules_retry(monkeypatch):
    """发送失败 → status=failed、next_retry_at≈+5min、last_error 记录。"""
    import app.core.monitor_engine as me

    async def fake_send(channels, title, description, **kwargs):
        return False

    monkeypatch.setattr(me, "send_to_all_channels", fake_send)
    db, plan = _delivery_env()
    event = _event()
    asyncio.run(_deliver_batch(db, plan, "BTCUSDT", "1h", [_item()], [event]))
    assert event.status == "failed"
    assert event.attempts == 1
    assert event.next_retry_at is not None
    assert "全部通知渠道投递失败" in event.last_error
