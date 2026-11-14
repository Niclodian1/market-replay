"""回归测试：ScaleSpec(k=1, pct, low) 必须与 v4 冻结规则逐根一致。

运行：python3 -m pytest scripts/analysis/transfer/test_parity.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import engine  # noqa: E402
from scale_spec import V4_IDENTITY, naive, scaled  # noqa: E402

ROOT = HERE.parents[2]


def _btc():
    return pd.read_parquet(ROOT / "data/crypto/BTC_USD_1day.parquet")


def test_raw_signals_match_mybuy_rules():
    import mybuy_rules as mr

    ref = [s["i"] for s in mr.algo_signals(mr.load_market())]
    res = engine.run(_btc(), V4_IDENTITY, with_outcome=False)
    assert [s["i"] for s in res["raw"]] == ref


def test_kept_signals_match_frozen_orders():
    frozen = json.loads((ROOT / "data/models/ai_ma20_v1_frozen.json").read_text())
    ref = [o["i"] for o in frozen["orders"]]
    res = engine.run(_btc(), V4_IDENTITY, with_outcome=False)
    assert [s["i"] for s in res["kept"]] == ref


def test_scaling_rules():
    s = scaled(60, alpha=0.65, H=0.69)
    assert s.k == 3 and s.ma_hi == 180 and s.macd == (36, 78, 27)
    assert s.cooldown == 16 and s.bars_below == 10 and s.reclaim == 3
    nv = naive(120)
    assert nv.cooldown == 8 and nv.ma_hi == 60 and nv.macd == (12, 26, 9)
