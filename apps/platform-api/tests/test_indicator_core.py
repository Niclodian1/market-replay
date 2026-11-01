"""indicator-core 契约与缠论 adapter 的回归测试（P0）。

守护三件事：
1. Event 的因果字段与去重键不可退化；
2. 缠论 adapter 的 id / 方向 / 参数哈希稳定；
3. 「全量注释按 confirmed_at 过滤」与「前缀重算」在真实数据上等价（防未来函数）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core import chanlun_service
from app.core.indicator_provider import ServiceChanlunProvider, get_registry
from app.indicator_core import Event, IndicatorRegistry, IndicatorSpec, params_hash
from app.indicator_core.adapters import build_chanlun_detectors

PARQUET_1D = Path(__file__).resolve().parents[3] / "data" / "crypto" / "BTC_USD_1day.parquet"

_requires_engine = pytest.mark.skipif(
    not PARQUET_1D.exists() or not chanlun_service.chanlun_available(),
    reason="fenxing/BTC 日线数据不可用",
)


def _load_1d() -> list[dict]:
    import pandas as pd

    df = pd.read_parquet(PARQUET_1D).sort_values("time").reset_index(drop=True)
    return [
        {
            "time": int(r.time),
            "open": float(r.open),
            "high": float(r.high),
            "low": float(r.low),
            "close": float(r.close),
            "volume": float(r.volume),
        }
        for r in df.itertuples()
    ]


# ── 契约本身 ────────────────────────────────────────────────────────────────

def test_params_hash_is_stable_and_order_insensitive() -> None:
    a = params_hash({"mode": "both", "include_forming": False})
    b = params_hash({"include_forming": False, "mode": "both"})
    assert a == b
    assert len(a) == 8
    assert a != params_hash({"mode": "stroke", "include_forming": False})
    assert params_hash(None) == params_hash({})


def test_event_uid_is_unique_per_bar_and_params() -> None:
    base = dict(
        indicator_id="chanlun.buy2",
        symbol="btc-usd",
        timeframe="4h",
        bar_time=1_700_000_000,
        confirmed_at=1_700_086_400,
        direction="long",
        price=65_000.0,
        params_hash="abcd1234",
    )
    e1 = Event(**base)
    e2 = Event(**{**base, "symbol": "BTC-USD"})
    e3 = Event(**{**base, "bar_time": 1_700_014_400})
    e4 = Event(**{**base, "params_hash": "ffff0000"})
    assert e1.uid == e2.uid  # 大小写归一
    assert e1.uid != e3.uid
    assert e1.uid != e4.uid
    assert e1.seen_at == e1.confirmed_at


def test_event_rejects_causality_violation_and_bad_price() -> None:
    with pytest.raises(ValueError, match="confirmed_at"):
        Event(
            indicator_id="chanlun.buy2", symbol="BTC-USD", timeframe="4h",
            bar_time=1_700_000_000, confirmed_at=1_699_900_000,
            direction="long", price=100.0,
        )
    with pytest.raises(ValueError, match="price"):
        Event(
            indicator_id="chanlun.buy2", symbol="BTC-USD", timeframe="4h",
            bar_time=1_700_000_000, confirmed_at=1_700_000_000,
            direction="long", price=0.0,
        )


def test_registry_catalog_exposes_chanlun_indicators() -> None:
    registry = get_registry()
    ids = registry.names
    assert "chanlun.buy2" in ids
    assert "ma.breakout" in ids
    assert "macd.divergence" in ids
    assert len([i for i in ids if i.startswith("chanlun.")]) == 10
    catalog = {item["id"]: item for item in registry.catalog()}
    buy2 = catalog["chanlun.buy2"]
    assert buy2["group"] == "chanlun"
    assert buy2["direction_support"] == ["long"]
    assert buy2["timeframes"] == ["1day", "4h", "1h"]
    assert buy2["params_schema"]["properties"]["mode"]["default"] == "both"


def test_registry_rejects_unknown_indicator_and_timeframe() -> None:
    registry = get_registry()
    bars = [{"time": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}] * 200
    with pytest.raises(KeyError):
        registry.run("chanlun.nope", bars, "BTC-USD", "1day")
    with pytest.raises(ValueError, match="不支持周期"):
        registry.run("chanlun.buy2", bars, "BTC-USD", "15min")


# ── 缠论 adapter ─────────────────────────────────────────────────────────────

@_requires_engine
def test_buy3_events_have_causal_fields_and_evidence() -> None:
    bars = _load_1d()
    events = get_registry().run("chanlun.buy3", bars, "BTC-USD", "1day")
    assert len(events) >= 10
    assert all(e.indicator_id == "chanlun.buy3" for e in events)
    assert all(e.direction == "long" for e in events)
    assert all(e.state == "confirmed" for e in events)
    assert all(e.confirmed_at >= e.bar_time for e in events)
    assert all(e.evidence.get("why") for e in events)
    assert all(e.evidence.get("k") == "三买" for e in events)
    assert len({e.uid for e in events}) == len(events)
    assert events == sorted(events, key=lambda e: (e.confirmed_at, e.bar_time, e.indicator_id))


@_requires_engine
def test_buy2_is_sparse_but_valid_on_btc_1d() -> None:
    """二买在 BTC 日线极稀疏（实测 1 次）——先记录事实，避免误选统计切片。"""
    bars = _load_1d()
    events = get_registry().run("chanlun.buy2", bars, "BTC-USD", "1day")
    assert 1 <= len(events) <= 5
    assert all(e.confirmed_at >= e.bar_time and e.price > 0 for e in events)


@_requires_engine
def test_mode_changes_params_hash_and_event_set() -> None:
    bars = _load_1d()
    registry = get_registry()
    stroke = registry.run("chanlun.buy3", bars, "BTC-USD", "1day", {"mode": "stroke"})
    seg = registry.run("chanlun.buy3", bars, "BTC-USD", "1day", {"mode": "seg"})
    both = registry.run("chanlun.buy3", bars, "BTC-USD", "1day", {"mode": "both"})
    assert {e.params_hash for e in stroke} != {e.params_hash for e in both}
    assert len(both) >= max(len(stroke), len(seg))
    assert len(stroke) + len(seg) >= len(both)  # both 会做 ±3bar 去重


@_requires_engine
def test_full_run_can_be_filtered_by_confirmed_at_equals_prefix_run() -> None:
    """核心因果断言：练习/统计依赖的「预计算 + confirmed_at 过滤」必须等价于前缀重算。"""
    bars = _load_1d()
    registry = get_registry()
    full = registry.run_all(bars, "BTC-USD", "1day")

    cursor_idx = int(len(bars) * 0.7)
    cursor_time = bars[cursor_idx]["time"]

    filtered = {
        (e.indicator_id, e.bar_time) for e in full if e.confirmed_at <= cursor_time
    }
    prefix = registry.run_all(bars[: cursor_idx + 1], "BTC-USD", "1day")
    prefix_keys = {(e.indicator_id, e.bar_time) for e in prefix}

    leaked = filtered - prefix_keys
    missing = prefix_keys - filtered
    assert leaked == set(), f"过滤后多出（未来泄漏）: {sorted(leaked)[:5]}"
    assert missing == set(), f"过滤后缺失（漏报）: {sorted(missing)[:5]}"


@_requires_engine
def test_provider_caches_one_engine_run_per_request() -> None:
    bars = _load_1d()
    provider = ServiceChanlunProvider()
    first = provider.analyze("BTC-USD", "1day", bars)
    second = provider.analyze("BTC-USD", "1day", bars)
    assert first is second

    detectors = build_chanlun_detectors(provider)
    registry = IndicatorRegistry(detectors)
    assert len(registry.names) == 10
    events = registry.run_all(bars, "BTC-USD", "1day")
    assert events


# ── API 出口（无 DB，直接调用路由函数）────────────────────────────────────────

def test_catalog_endpoint_lists_all_indicators() -> None:
    from app.api.indicator_catalog_api import list_indicators

    payload = list_indicators()
    assert payload["count"] == 12
    ids = {item["id"] for item in payload["indicators"]}
    assert {"chanlun.buy3", "chanlun.beichi.buy", "chanlun.sell2"} <= ids
    assert next(i for i in payload["indicators"] if i["id"] == "chanlun.buy3")["group"] == "chanlun"


def test_catalog_endpoint_returns_404_for_unknown() -> None:
    from fastapi import HTTPException

    from app.api.indicator_catalog_api import get_indicator

    with pytest.raises(HTTPException) as exc:
        get_indicator("chanlun.nope")
    assert exc.value.status_code == 404


def test_routers_are_registered() -> None:
    from app.api.router import api_router

    paths = {route.path for route in api_router.routes}
    assert "/indicator-catalog" in paths
    assert "/indicator-catalog/{indicator_id}" in paths
    assert "/indicator-events" in paths


@_requires_engine
def test_events_endpoint_supports_strict_as_of_and_prefix_equivalence() -> None:
    from app.api.indicator_catalog_api import CandleIn, EventsRequest, compute_events

    bars = _load_1d()[:600]
    candles = [CandleIn(**bar) for bar in bars]

    full = compute_events(
        EventsRequest(symbol="BTC-USD", timeframe="1day", candles=candles)
    )
    assert full["count"] > 0
    assert full["events"] == sorted(
        full["events"], key=lambda e: (e["confirmed_at"], e["bar_time"], e["indicator_id"])
    )

    cut = 450
    cursor_time = bars[cut]["time"]
    strict = compute_events(
        EventsRequest(
            symbol="BTC-USD", timeframe="1day", candles=candles, as_of=cursor_time
        )
    )
    strict_keys = {(e["indicator_id"], e["bar_time"]) for e in strict["events"]}
    filtered_keys = {
        (e["indicator_id"], e["bar_time"])
        for e in full["events"]
        if e["confirmed_at"] <= cursor_time
    }
    assert strict_keys == filtered_keys


# ── 数值序列（Series）────────────────────────────────────────────────────────

def _oscillating_bars(n: int = 240, amplitude: float = 12.0) -> list[dict]:
    """围绕均线上下摆动，保证有反复穿越（用于突破类指标）。"""
    import math

    bars = []
    prev = 100.0 + amplitude * math.sin(-1 / 7.0)
    for i in range(n):
        price = 100.0 + amplitude * math.sin(i / 7.0)
        bars.append(
            {
                "time": 1_700_000_000 + i * 86_400,
                "open": prev,
                "high": max(prev, price) + 0.5,
                "low": min(prev, price) - 0.5,
                "close": price,
                "volume": 10.0,
            }
        )
        prev = price
    return bars


def _synthetic_bars(n: int = 120) -> list[dict]:
    bars = []
    price = 100.0
    for i in range(n):
        price += 1.0 if i % 3 else -0.5
        bars.append(
            {
                "time": 1_700_000_000 + i * 86_400,
                "open": price - 0.4,
                "high": price + 1.0,
                "low": price - 1.0,
                "close": price,
                "volume": 10.0,
            }
        )
    return bars


def test_sma_and_ema_warmup() -> None:
    from app.indicator_core.series import ema, sma

    assert sma([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]
    flat = ema([10.0] * 10, 3)
    assert flat[:2] == [None, None]
    assert flat[2:] == [10.0] * 8


def test_macd_hist_is_double_gap() -> None:
    from app.indicator_core.series import macd_series

    bars = _synthetic_bars(200)
    dif, dea, hist = macd_series(bars)
    assert dif.key == "macd_dif" and hist.kind == "histogram"
    for d, e, h in zip(dif.values, dea.values, hist.values):
        if d is None or e is None:
            assert h is None
        else:
            assert abs(h - (d - e) * 2) < 1e-9


def test_rsi_is_bounded_and_saturates_on_uptrend() -> None:
    from app.indicator_core.series import rsi_series

    bars = [{"time": i, "open": i, "high": i + 1, "low": i - 1, "close": float(i + 1)} for i in range(60)]
    (rsi,) = rsi_series(bars, period=14)
    assert rsi.values[13] is None
    assert rsi.values[14] == 100.0
    assert all(v is None or 0.0 <= v <= 100.0 for v in rsi.values)


def test_atr_matches_true_range_on_flat_bars() -> None:
    from app.indicator_core.series import atr_series

    bars = [{"time": i, "open": 100, "high": 102, "low": 98, "close": 100} for i in range(40)]
    (atr,) = atr_series(bars, period=14)
    assert atr.values[12] is None
    assert abs(atr.values[13] - 4.0) < 1e-9


def test_compute_series_rejects_unknown_and_bad_params() -> None:
    from app.indicator_core.series import compute_series

    with pytest.raises(ValueError, match="未知序列"):
        compute_series(_synthetic_bars(30), [{"id": "nope"}])
    with pytest.raises(ValueError, match="参数非法"):
        compute_series(_synthetic_bars(30), [{"id": "ma", "params": {"bogus": 1}}])


def test_series_endpoint_aligns_values_with_candles() -> None:
    from app.api.indicator_catalog_api import (
        CandleIn,
        SeriesComputeRequest,
        SeriesRequest,
        compute,
        list_series_catalog,
    )

    catalog = list_series_catalog()
    assert {item["id"] for item in catalog["series"]} == {"ma", "macd", "rsi", "atr"}

    bars = _synthetic_bars(150)
    payload = compute(
        SeriesComputeRequest(
            symbol="BTC-USD",
            timeframe="1day",
            candles=[CandleIn(**bar) for bar in bars],
            series=[
                SeriesRequest(id="ma", params={"periods": [20, 60]}),
                SeriesRequest(id="macd"),
                SeriesRequest(id="atr", params={"period": 14}),
            ],
        )
    )
    assert payload["count"] == 150
    by_key = {item["key"]: item for item in payload["series"]}
    assert {"ma_sma_20", "ma_sma_60", "macd_dif", "macd_dea", "macd_hist", "atr"} <= set(by_key)
    assert len(by_key["ma_sma_20"]["values"]) == 150
    assert by_key["ma_sma_20"]["values"][:19] == [None] * 19
    assert by_key["ma_sma_20"]["values"][19] is not None

    cut = payload["candles"][99]["time"]
    truncated = compute(
        SeriesComputeRequest(
            symbol="BTC-USD",
            timeframe="1day",
            candles=[CandleIn(**bar) for bar in bars],
            series=[SeriesRequest(id="ma", params={"periods": [20]})],
            as_of=cut,
        )
    )
    assert truncated["count"] == 100
    assert len(truncated["series"][0]["values"]) == 100


@_requires_engine
def test_series_endpoint_can_include_events_consistent_with_events_endpoint() -> None:
    from app.api.indicator_catalog_api import (
        CandleIn,
        EventsRequest,
        SeriesComputeRequest,
        compute,
        compute_events,
    )

    bars = _load_1d()[:400]
    candles = [CandleIn(**bar) for bar in bars]
    only_events = compute_events(
        EventsRequest(symbol="BTC-USD", timeframe="1day", candles=candles)
    )
    both = compute(
        SeriesComputeRequest(
            symbol="BTC-USD",
            timeframe="1day",
            candles=candles,
            series=[],
            events=True,
        )
    )
    assert {e["uid"] for e in both["events"]} == {e["uid"] for e in only_events["events"]}


# ── 均线突破 ────────────────────────────────────────────────────────────────

@_requires_engine
def test_ma_breakout_scans_history_and_dedupes_by_period() -> None:
    bars = _load_1d()
    events = get_registry().run(
        "ma.breakout", bars, "BTC-USD", "1day", {"periods": [20, 60]}
    )
    assert len(events) >= 10
    assert {e.event_key for e in events} == {"ma20", "ma60"}
    assert all(e.confirmed_at == e.bar_time for e in events)
    assert all(e.direction in ("long", "short") for e in events)
    assert len({e.uid for e in events}) == len(events)
    assert all(e.evidence["ma"]["period"] in (20, 60) for e in events)


def test_ma_breakout_params_hash_changes_with_periods() -> None:
    bars = _oscillating_bars(240)
    registry = get_registry()
    a = registry.run("ma.breakout", bars, "BTC-USD", "1day", {"periods": [20]})
    b = registry.run("ma.breakout", bars, "BTC-USD", "1day", {"periods": [20, 60]})
    assert a and b
    assert {e.params_hash for e in a} != {e.params_hash for e in b}


def test_ma_breakout_detects_cross_and_filters_body() -> None:
    from app.indicator_core.adapters.ma import MaBreakoutDetector

    bars = _oscillating_bars(240)
    detector = MaBreakoutDetector()
    loose = detector.detect(bars, "BTC-USD", "1day", {"periods": [20], "min_body_atr": 0.0, "min_close_location": 0.0})
    strict = detector.detect(
        bars, "BTC-USD", "1day",
        {"periods": [20], "min_body_atr": 5.0, "min_close_location": 0.99},
    )
    assert len(loose) > len(strict)


# ── MACD 背离 ───────────────────────────────────────────────────────────────

@_requires_engine
def test_macd_divergence_is_lagged_three_bars_and_filterable() -> None:
    bars = _load_1d()
    registry = get_registry()
    confirmed = registry.run("macd.divergence", bars, "BTC-USD", "1day")
    assert len(confirmed) >= 50
    assert all(e.state == "confirmed" for e in confirmed)
    assert all(e.confirmed_at >= e.bar_time for e in confirmed)
    assert any(e.confirmed_at > e.bar_time for e in confirmed), "背离应滞后确认"
    assert all(0 < (e.strength or 0) <= 1 for e in confirmed)
    assert {e.direction for e in confirmed} == {"long", "short"}
    assert len({e.uid for e in confirmed}) == len(confirmed)

    pending = registry.run(
        "macd.divergence", bars, "BTC-USD", "1day", {"include_pending": True}
    )
    assert len(pending) >= len(confirmed)

    strong = registry.run(
        "macd.divergence", bars, "BTC-USD", "1day", {"min_strength": 0.5}
    )
    assert 0 < len(strong) < len(confirmed)
    assert all((e.strength or 0) >= 0.5 for e in strong)
