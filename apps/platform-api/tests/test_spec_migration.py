"""真实旧实验 → spec 迁移：文件可校验、可复现、在本地数据上能跑出交易。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.indicator_provider import get_registry
from app.experiment_core.runner import run_experiment
from app.experiment_core.service import load_data
from app.experiment_core.spec import parse_spec

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC_PATH = REPO_ROOT / "experiments" / "specs" / "ma_breakout_2r.spec.json"


def _raw() -> dict:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def test_migrated_spec_is_valid_and_stable() -> None:
    assert SPEC_PATH.exists(), "迁移后的 spec 文件必须入库"
    raw = _raw()
    spec = parse_spec(raw)
    assert spec.name.startswith("ma20 breakout")
    assert list(spec.symbols) == ["BTC-USD"]
    assert list(spec.timeframes) == ["1day"]
    assert spec.raw["entry"]["all"][0]["indicator"] == "ma.breakout"
    assert spec.raw["costs"] == {"fee_bps": 10, "slippage_bps": 5}
    assert spec.raw["validation"]["min_trades"] == 5
    # 同一份 spec 必须得到同一个 spec_hash（发布门禁依赖它）
    assert spec.spec_hash == parse_spec(_raw()).spec_hash
    assert spec.spec_hash


def test_migrated_spec_rejects_unknown_field() -> None:
    raw = _raw()
    raw["entry"]["all"][0]["mystery"] = 1
    with pytest.raises(Exception):
        parse_spec(raw)


def test_migrated_spec_runs_on_local_data() -> None:
    spec = parse_spec(_raw())
    data = load_data(spec)
    bars = data.get(("BTC-USD", "1day")) or []
    if not bars:
        pytest.skip("本地缺少 BTC-USD 1day 数据（CI 无行情文件时跳过）")

    result = run_experiment(spec, data, get_registry())
    metrics = result.metrics
    assert metrics["n"] >= spec.raw["validation"]["min_trades"]
    assert metrics["by_exit_reason"], "至少要能区分 stop/target/time 出场"
    assert metrics["n"] == len(result.trades)
    # 交易明细要能直接落到 ExperimentTrade（含入场事件 uid，可跳回当时）
    assert all(trade.entry_event_uid for trade in result.trades)
