"""统一策略实验台 API 的纯逻辑测试(catalog + 信封映射,不依赖 DB)。

需要 fastapi(随 requirements.txt 安装);缺失时跳过。
"""
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from app.api import strategy_lab_api as lab  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "strategy_c_candles.json"


def test_catalog_contains_c_and_indicators():
    cat = lab.get_catalog()["strategies"]
    ids = {s["id"] for s in cat}
    assert "strategy_c" in ids
    assert {"ma_cross", "rsi", "macd"} <= ids
    c = next(s for s in cat if s["id"] == "strategy_c")
    assert c["kind"] == "multi_position"
    assert c["variant_axis"] == "k"


def test_strategy_c_envelope_shape():
    candles = json.loads(FIXTURE.read_text())
    env = lab._run_strategy_c("BTC-USD", "1day", candles)
    assert env["strategy_id"] == "strategy_c"
    assert env["kind"] == "multi_position"
    assert env["best_variant"] == "x2"
    assert {"ma_lines"} <= env["overlays"].keys()
    ids = [v["id"] for v in env["variants"]]
    assert ids == sorted(ids)  # 变体有序
    for v in env["variants"]:
        assert {"id", "label", "params", "stats", "trades"} <= v.keys()
        assert {"total_return", "n_trades", "win_rate"} <= v["stats"].keys()
