"""静态出库：产物结构、manifest 与跳过逻辑。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core import web_data

PARQUET_1D = Path(__file__).resolve().parents[3] / "data" / "crypto" / "BTC_USD_1day.parquet"
pytestmark = pytest.mark.skipif(not PARQUET_1D.exists(), reason="BTC 日线数据不可用")


def test_build_bundle_contains_candles_series_events() -> None:
    payload = web_data.build_bundle("BTC-USD", "1day", limit=300)
    assert payload is not None
    assert len(payload["candles"]) == 300
    assert payload["events"]
    keys = {item["key"] for item in payload["series"]}
    assert {"ma_sma_20", "ma_sma_60"} <= keys
    assert all(len(item["values"]) == 300 for item in payload["series"])


def test_write_bundles_and_manifest(tmp_path: Path) -> None:
    manifest = web_data.write_bundles(
        tmp_path,
        [("BTC-USD", "1day"), ("NOPE-USD", "1day")],
        limit=200,
    )
    assert manifest["schema"] == "web-data/1"
    assert len(manifest["symbols"]) == 1
    btc = manifest["symbols"][0]
    assert btc["symbol"] == "BTC-USD"
    info = btc["timeframes"]["1day"]
    assert info["bars"] == 200
    assert info["events"] > 0
    assert (tmp_path / info["path"]).exists()
    assert (tmp_path / "manifest.json").exists()
    assert manifest["skipped"][0]["symbol"] == "NOPE-USD"
