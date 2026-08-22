"""Tests for the 缠论 service: analysis bridge, signal normalization, report mapping."""
from __future__ import annotations

import math
import time

import pytest

from app.core import chanlun_service
from app.core.chanlun_service import ChanlunUnavailable


def _synthetic_candles(n: int = 220, *, base: float = 100.0, step: int = 86_400) -> list[dict]:
    out = []
    for i in range(n):
        wave = math.sin(i / 12.0) * 8.0 + (i / n) * 20.0
        close = base + wave
        out.append({
            "time": 1_700_000_000 + i * step,
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1000.0 + i,
        })
    return out


def _canned_levels(sig_raw: list[dict], extra: dict | None = None) -> dict:
    return {
        "1D": {
            "buyPointsSegmentRaw": sig_raw,
            "sellPointsSegmentRaw": [],
            "beichiAlertsRaw": [],
            "beichiPointsRaw": [],
            **(extra or {}),
        }
    }


def test_full_set_runs_link_levels_and_fills_sub_upper():
    if not chanlun_service.chanlun_available():
        pytest.skip("fenxing 不可用")
    levels = chanlun_service.analyze_symbol({
        "1day": _synthetic_candles(220),
        "4h": _synthetic_candles(260, step=4 * 3600),
        "1h": _synthetic_candles(300, step=3600),
    })
    assert set(levels) == {"1D", "4H", "1H"}
    for tf, lvl in levels.items():
        for p in lvl["_internal"]["buys"]["seg"] + lvl["_internal"]["sells"]["seg"]:
            assert p.get("sub"), f"{tf} seg 点缺 sub"
            assert p.get("upper")


def test_single_day_fills_default_sub_upper():
    if not chanlun_service.chanlun_available():
        pytest.skip("fenxing 不可用")
    levels = chanlun_service.analyze_symbol({"1day": _synthetic_candles(220)})
    assert set(levels) == {"1D"}
    lvl = levels["1D"]
    for p in lvl["_internal"]["buys"]["seg"] + lvl["_internal"]["sells"]["seg"]:
        assert p.get("sub"), "单周期 seg 点缺默认 sub"
        assert p.get("upper") == "—"


def test_unavailable_raises_on_analyze(monkeypatch):
    monkeypatch.setattr(chanlun_service, "_FENXING_AVAILABLE", False)
    with pytest.raises(ChanlunUnavailable):
        chanlun_service.analyze_symbol({"1day": _synthetic_candles(220)})


def test_extract_signals_maps_kind_direction_and_dedupe_key(monkeypatch):
    now = int(time.time())
    levels = _canned_levels([
        {"t": now - 1000, "p": 100.0, "k": "一买", "state": "confirmed",
         "tier": "明显", "why": "C段背驰", "sub": "", "upper": "", "confirmedT": now},
        {"t": now - 500, "p": 101.0, "k": "三卖", "state": "forming",
         "tier": None, "why": "不破前低", "sub": "", "upper": "", "confirmedT": None},
    ])
    signals = chanlun_service.extract_signals("TEST", levels)
    assert len(signals) == 2
    buy = signals[0]
    assert (buy["kind"], buy["direction"]) == ("buy", "long")
    sell = signals[1]
    assert (sell["kind"], sell["direction"]) == ("sell3", "short")
    assert sell["state"] == "forming"
    assert chanlun_service._dedupe_key(buy) == (
        f"TEST:1day:seg:一买:{buy['t']}"
    )
    assert chanlun_service._dedupe_key(sell) != chanlun_service._dedupe_key(buy)


def test_extract_signals_covers_fast_layer(monkeypatch):
    now = int(time.time())
    levels = _canned_levels([], extra={
        "beichiAlertsRaw": [{"t": now - 200, "p": 100.0, "side": "买", "why": "速率告警"}],
        "beichiPointsRaw": [{
            "t": now - 100, "p": 100.5, "k": "背驰买", "state": "forming",
            "trigger": "MACD柱缩短", "ratio": 0.42, "alertT": now - 200,
            "confirmedT": None, "why": "背驰入场",
        }],
    })
    signals = chanlun_service.extract_signals("TEST", levels)
    assert len(signals) == 2
    alert = signals[0]
    assert (alert["mode"], alert["kind"], alert["direction"]) == ("fast", "alert", "long")
    assert alert["state"] is None
    point = signals[1]
    assert (point["mode"], point["kind"], point["direction"]) == ("fast", "beichi_buy", "long")
    assert point["ratio"] == 0.42
    assert point["trigger"] == "MACD柱缩短"


def test_detect_chanlun_opportunities_produces_valid_event_and_strength():
    now = int(time.time())
    levels = _canned_levels([{
        "t": now - 3600, "p": 100.0, "k": "一买", "state": "confirmed",
        "tier": "明显", "why": "背驰", "sub": "", "upper": "", "confirmedT": now,
    }])
    items = chanlun_service.detect_chanlun_opportunities(
        "crypto", "TEST", "测试", levels=levels
    )
    assert len(items) == 1
    item = items[0]
    assert item.event_type == "chanlun_buy1"
    assert item.direction == "long"
    assert item.stars == 5
    assert "一买" in item.summary


def test_detect_chanlun_opportunities_filters_forming_and_old():
    now = int(time.time())
    levels = _canned_levels([
        {"t": now - 3600, "p": 100.0, "k": "一买", "state": "confirmed",
         "tier": "明显", "why": "x", "sub": "", "upper": "", "confirmedT": now},
        {"t": now - 3600, "p": 100.0, "k": "二买", "state": "forming",
         "tier": None, "why": "x", "sub": "", "upper": "", "confirmedT": None},
        {"t": now - 20 * 86_400, "p": 100.0, "k": "三买", "state": "confirmed",
         "tier": None, "why": "x", "sub": "", "upper": "", "confirmedT": now},
    ])
    items = chanlun_service.detect_chanlun_opportunities(
        "crypto", "TEST", "测试", levels=levels
    )
    assert [item.event_type for item in items] == ["chanlun_buy1"]


def test_detect_chanlun_opportunities_returns_empty_when_unavailable(monkeypatch):
    monkeypatch.setattr(chanlun_service, "_FENXING_AVAILABLE", False)
    assert chanlun_service.detect_chanlun_opportunities(
        "crypto", "TEST", "测试", candles=_synthetic_candles(130)
    ) == []
