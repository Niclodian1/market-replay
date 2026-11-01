"""指标层 golden 快照：12 个指标在 BTC 1D 尾段的事件必须逐字段稳定。

快照由 ``python3 scripts/export_indicator_golden.py --bars 600`` 生成。
任何 adapter / 序列 / 因果字段的口径变化都会在这里和 CI 里暴露。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core import chanlun_service
from app.core.indicator_provider import get_registry

PARQUET_1D = Path(__file__).resolve().parents[3] / "data" / "crypto" / "BTC_USD_1day.parquet"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "indicator_events_golden.json"

_requires_engine = pytest.mark.skipif(
    not PARQUET_1D.exists() or not FIXTURE.exists() or not chanlun_service.chanlun_available(),
    reason="BTC 日线数据 / golden 快照 / 缠论引擎不可用",
)


def _load_tail(bars: int) -> list[dict]:
    import pandas as pd

    frame = pd.read_parquet(PARQUET_1D).sort_values("time").tail(bars)
    return [
        {
            "time": int(row.time),
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "volume": float(row.volume),
        }
        for row in frame.itertuples()
    ]


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@_requires_engine
def test_indicator_events_match_golden(golden: dict) -> None:
    registry = get_registry()
    candles = _load_tail(golden["bars"])
    events = registry.run_many(registry.names, candles, golden["symbol"], golden["timeframe"], {})
    assert [event.to_dict() for event in events] == golden["events"]


@_requires_engine
def test_golden_covers_every_registered_indicator(golden: dict) -> None:
    assert golden["indicator_ids"] == sorted(get_registry().names)
    assert len(golden["indicator_ids"]) == 12
    assert len(golden["events"]) == 112


@_requires_engine
def test_golden_events_keep_causality_and_unique_uid(golden: dict) -> None:
    uids = [event["uid"] for event in golden["events"]]
    assert len(uids) == len(set(uids))
    for event in golden["events"]:
        assert event["confirmed_at"] >= event["bar_time"]
        assert event["indicator_id"] in golden["indicator_ids"]
