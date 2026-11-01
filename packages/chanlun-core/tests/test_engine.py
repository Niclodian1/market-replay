"""缠论引擎自有测试：合成随机游走数据，不依赖仓库行情文件。"""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "packages" / "chanlun-core"))

from chanlun_core import analyze  # noqa: E402
from chanlun_core.chanlun import macd_series  # noqa: E402
from chanlun_core.fractals import (  # noqa: E402
    enforce_alternation,
    find_fractals,
    process_inclusions,
)

BARS = 300
EPOCH0 = 1_600_000_000
DAY = 86_400

ANALYZE_KEYS = {
    "meta",
    "candles",
    "mergedCandles",
    "strokesRaw",
    "segmentsRaw",
    "pivotsStrokeRaw",
    "pivotsSegmentRaw",
    "buyPointsStrokeRaw",
    "sellPointsStrokeRaw",
    "beichiPointsRaw",
    "topMarkers",
    "bottomMarkers",
    "macdDiff",
    "macdDea",
    "macdHist",
}


def _bars(count: int = BARS, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    price = 100.0
    out: list[dict] = []
    for i in range(count):
        price *= 1 + rng.uniform(-0.03, 0.03)
        high = price * (1 + rng.uniform(0.002, 0.02))
        low = price * (1 - rng.uniform(0.002, 0.02))
        out.append({
            "idx": i,
            "open": price,
            "high": max(high, price),
            "low": min(low, price),
            "close": price,
            "volume": 1.0,
        })
    return out


def _epochs(count: int = BARS) -> list[int]:
    return [EPOCH0 + i * DAY for i in range(count)]


@pytest.fixture(scope="module")
def result() -> dict:
    return analyze(_bars(), _epochs(), "1D")


def test_inclusion_and_fractals_on_synthetic_data() -> None:
    bars = _bars()
    merged = process_inclusions(bars)
    assert 0 < len(merged) < len(bars)
    for bar in merged:
        assert bar["high"] >= bar["low"]
        assert bar["start"] <= bar["end"]

    tops, bottoms = find_fractals(merged, bars)
    assert tops and bottoms
    tops, bottoms = enforce_alternation(merged, bars, tops, bottoms)
    assert len(tops) + len(bottoms) >= 2


def test_analyze_exposes_expected_keys(result: dict) -> None:
    assert ANALYZE_KEYS <= set(result)
    meta = result["meta"]
    assert meta["nBars"] == BARS
    assert meta["nStrokes"] == len(result["strokesRaw"])
    assert meta["nSegments"] == len(result["segmentsRaw"])
    assert meta["nPivotsStroke"] == len(result["pivotsStrokeRaw"])
    assert result["strokesRaw"], "合成数据应至少产出一笔"


def test_macd_matches_direct_recursion(result: dict) -> None:
    closes = [bar["close"] for bar in _bars()]
    diff, dea, hist = macd_series(closes)
    assert len(hist) == len(result["macdHist"])
    assert result["macdHist"] == [float(f"{x:.6g}") for x in hist]
    for i, value in enumerate(diff):
        assert math.isclose(result["macdDiff"][i], float(f"{value:.6g}"), rel_tol=1e-9, abs_tol=1e-9)


def test_points_respect_causality(result: dict) -> None:
    points = result["buyPointsStrokeRaw"] + result["sellPointsStrokeRaw"]
    assert points, "合成数据应产出买卖点"
    for point in points:
        assert point.get("confirmedT") is not None
        assert point["confirmedT"] >= point["t"]
        assert point.get("state") in (None, "confirmed")


def test_analyze_is_deterministic(result: dict) -> None:
    again = analyze(_bars(), _epochs(), "1D")
    assert json.dumps(result, sort_keys=True, default=str) == json.dumps(again, sort_keys=True, default=str)


def test_chanlun_web_shim_reexports_engine() -> None:
    sys.path.insert(0, str(REPO_ROOT / "apps" / "chanlun-web"))
    import chanlun  # noqa: PLC0415
    import chanlun_core.chanlun as engine  # noqa: PLC0415

    assert chanlun.analyze is engine.analyze
    assert chanlun.macd_series is engine.macd_series
    assert hasattr(chanlun, "link_levels")


CAUSAL_ARRAYS = (
    "buyPointsStrokeRaw", "sellPointsStrokeRaw",
    "buyPointsSegmentRaw", "sellPointsSegmentRaw",
    "beichiPointsRaw", "beichiPointsSegmentRaw",
    "panbeiPointsRaw", "panbeiPointsSegmentRaw",
)


def _point_keys(level: dict) -> set[tuple]:
    return {
        (name, p.get("k"), int(p["t"]), int(p.get("confirmedT") or 0), round(float(p["p"]), 4))
        for name in CAUSAL_ARRAYS
        for p in level.get(name) or []
    }


def test_prefix_recompute_equals_confirmed_slice() -> None:
    """因果契约: 前缀重算 == 全量按 confirmedT 过滤（0 泄漏 / 0 漏报）。

    前缀重算是「实盘在 T 时刻能看到什么」的基准；若全量结果里某点的
    confirmedT > T 却出现在前缀里 → 用了未来 K 线；反之 → 漏报。
    """
    bars, epochs = _bars(600, seed=11), _epochs(600)
    full = analyze(bars, epochs, "1D")
    full_points = {(k[0], k[1], k[2], k[3], k[4]) for k in _point_keys(full)}
    assert full_points, "合成数据应产出因果点"
    for cut in (150, 300, 450, 550):
        prefix = analyze(bars[: cut + 1], epochs[: cut + 1], "1D")
        got = _point_keys(prefix)
        expect = {k for k in full_points if k[3] <= epochs[cut]}
        assert got == expect, (
            f"cut={cut} 泄漏={sorted(got - expect)[:3]} 漏报={sorted(expect - got)[:3]}"
        )


def test_caches_do_not_accumulate_across_calls() -> None:
    """因果链模块级缓存必须随 (bars, 长度) 变化重置，否则长驻进程内存无界。

    回归 2026-10 审计: _loc_val_cache 曾按 id(bars) 累积且从不清理，
    12 次调用留下 12 份极值表；id() 被新列表复用时还会读到别的序列的价格。
    """
    from chanlun_core import chanlun as engine_mod  # noqa: PLC0415

    bars, epochs = _bars(400, seed=3), _epochs(400)
    first = analyze(bars[:300], epochs[:300], "1D")
    engine_mod._loc_val_cache.clear()
    engine_mod._causal_cache_owner = None
    analyze(bars[:300], epochs[:300], "1D")
    size_after_one = len(engine_mod._loc_val_cache)
    for cut in (320, 340, 360, 380, 400):
        analyze(bars[:cut], epochs[:cut], "1D")
    assert len(engine_mod._loc_val_cache) <= size_after_one + 120, "缓存随调用次数累积"
    assert len(engine_mod._enforce_state) == 5
    # 同一 bars 对象重复调用必须给出相同结果（缓存共享不改变语义）
    again = analyze(bars[:300], epochs[:300], "1D")
    assert json.dumps(first, sort_keys=True, default=str) == json.dumps(again, sort_keys=True, default=str)
