"""三段背离引擎: 因果性(零未来函数) + 基准集回归测试。

运行: python3 -m pytest scripts/strategies/tests/test_macd_3seg_causality.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from macd_3seg_divergence import MACD3SegDivergence, Params  # noqa: E402
from run_macd_3seg_validation import GROUND_TRUTH, GT_STOPS, hit_table, load  # noqa: E402

KEYS = ("kind", "label", "bar", "time", "price", "side", "attempt", "stop_price", "segments", "note")


def _events(df: pd.DataFrame, upto: int | None = None) -> list[dict]:
    eng = MACD3SegDivergence(df.iloc[: upto + 1] if upto is not None else df, Params()).run()
    out = eng.result()["events"]
    return [{k: e[k] for k in KEYS} for e in out if upto is None or e["bar"] <= upto]


@pytest.fixture(scope="module")
def d1() -> pd.DataFrame:
    return load("data/crypto/BTCUSDT_1d.parquet")


@pytest.fixture(scope="module")
def h4() -> pd.DataFrame:
    return load("data/crypto/BTCUSDT_4h.parquet")


def test_ground_truth_100pct(d1):
    res = MACD3SegDivergence(d1, Params()).run().result()
    rows, extras = hit_table(res)
    miss = [r["date"] for r in rows if not r["hit"]]
    assert not miss, f"未命中: {miss}"
    bad_stop = [r["date"] for r in rows if r["stop_check"] is False]
    assert not bad_stop, f"次日止损日期不符: {bad_stop}"
    in_window = [e["date"] for e in extras if "2018-01-01" <= e["date"] <= "2025-11-09"]
    assert not in_window, f"原文统计区间内出现额外信号: {in_window}"
    assert len(rows) == len(GROUND_TRUTH) and len(GT_STOPS) == 4


def test_macd_prefix_stable(d1):
    """MACD 递推 EMA: 截断与全量在重叠区逐点相等。"""
    full = MACD3SegDivergence(d1).hist
    for t in (100, 777, 1500, len(d1) - 2):
        part = MACD3SegDivergence(d1.iloc[: t + 1]).hist
        np.testing.assert_allclose(part, full[: t + 1], rtol=0, atol=1e-9)


def test_truncation_equivalence_1d(d1):
    """在每个信号/止损事件当根 T 截断: T 及以前的全部事件与全量运行逐字段一致。"""
    full = _events(d1)
    bars = sorted({e["bar"] for e in full})
    for T in bars:
        part = _events(d1, T)
        want = [e for e in full if e["bar"] <= T]
        assert part == want, f"T={T} 截断后事件漂移"


def test_truncation_random_bars_1d(d1):
    full = _events(d1)
    rng = np.random.default_rng(20261104)
    for T in rng.integers(200, len(d1) - 1, size=12):
        assert _events(d1, int(T)) == [e for e in full if e["bar"] <= T]


def test_future_poison_does_not_leak(d1):
    """把 T 之后的行情替换成随机噪声: T 及以前的事件必须完全不变。"""
    full = _events(d1)
    rng = np.random.default_rng(7)
    for T in sorted({e["bar"] for e in full if e["kind"] in ("buy", "sell")}):
        bad = d1.copy()
        n = len(bad) - T - 1
        if n <= 0:
            continue
        noise = rng.uniform(0.3, 3.0, size=n)
        for col in ("open", "high", "low", "close"):
            bad.loc[T + 1:, col] = bad[col].iloc[T] * noise
        bad.loc[T + 1:, "high"] = bad.loc[T + 1:, ["open", "high", "low", "close"]].max(axis=1)
        bad.loc[T + 1:, "low"] = bad.loc[T + 1:, ["open", "high", "low", "close"]].min(axis=1)
        got = [e for e in _events(bad) if e["bar"] <= T]
        assert got == [e for e in full if e["bar"] <= T], f"T={T} 受未来数据影响"


def test_truncation_equivalence_4h_sample(h4):
    full = _events(h4)
    entries = [e["bar"] for e in full if e["kind"] in ("buy", "sell")]
    for T in entries[:: max(1, len(entries) // 10)]:
        assert _events(h4, T) == [e for e in full if e["bar"] <= T]
