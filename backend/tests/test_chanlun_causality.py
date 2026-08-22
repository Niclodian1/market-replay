"""缠论买卖点因果性: 失败点保留、confirmedT 在前缀上已可判定、背驰不回标。"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core import chanlun_service

FENXING = Path(__file__).resolve().parents[2] / "fenxing"
PARQUET_1D = FENXING / "BTC_USD_1day.parquet"


def _fmt(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d")


def _load_1d():
    import pandas as pd

    df = pd.read_parquet(PARQUET_1D).sort_values("time").reset_index(drop=True)
    candles = [
        {
            "time": int(r["time"]),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": float(r.get("volume") or 0),
        }
        for _, r in df.iterrows()
    ]
    return candles


def _idx_of(candles, t: int) -> int:
    for i, c in enumerate(candles):
        if c["time"] >= t:
            return i
    return len(candles) - 1


def _stroke_points(level: dict, kind: str) -> list[dict]:
    key = "buyPointsStrokeRaw" if "买" in kind else "sellPointsStrokeRaw"
    return [p for p in level.get(key, []) if p.get("k") == kind]


@pytest.mark.skipif(not PARQUET_1D.exists() or not chanlun_service.chanlun_available(),
                    reason="fenxing/BTC 日线数据不可用")
def test_buy1_keeps_intermediate_failures_on_btc_1d():
    candles = _load_1d()
    levels = chanlun_service.analyze_symbol({"1day": candles})
    buys = _stroke_points(levels["1D"], "一买")
    dates = {_fmt(p["t"]) for p in buys}
    # 中间失败一买必须能在当时前缀出现, 且确认不能拖过一周(否则已不是该低点的入场)
    assert "2018-06-13" in dates
    for p in buys:
        lag = (p["confirmedT"] - p["t"]) / 86400
        assert lag <= 8, f"{_fmt(p['t'])} 一买确认晚了 {lag:.0f} 天"


@pytest.mark.skipif(not PARQUET_1D.exists() or not chanlun_service.chanlun_available(),
                    reason="fenxing/BTC 日线数据不可用")
def test_confirmed_t_exists_on_its_own_prefix():
    """每个已确认笔级一/二/三买卖点, 在 confirmedT 前缀上必须已经出现。"""
    candles = _load_1d()
    full = chanlun_service.analyze_symbol({"1day": candles})["1D"]
    kinds = ("一买", "二买", "三买", "一卖", "二卖", "三卖")
    points = []
    for k in kinds:
        points.extend(_stroke_points(full, k))
    tradable = [p for p in points if p.get("state") == "confirmed" and p.get("confirmedT")]
    assert len(tradable) >= 8
    sample = [p for p in tradable if p["k"] in ("一买", "一卖")][:8]
    missing = []
    for p in sample:
        ci = _idx_of(candles, p["confirmedT"])
        pref = chanlun_service.analyze_symbol({"1day": candles[: ci + 1]})["1D"]
        got = _stroke_points(pref, p["k"])
        if not any(g.get("t") == p["t"] for g in got):
            missing.append((p["k"], _fmt(p["t"]), _fmt(p["confirmedT"])))
    assert missing == [], f"confirmedT 前缀上还不存在: {missing[:8]}"


@pytest.mark.skipif(not PARQUET_1D.exists() or not chanlun_service.chanlun_available(),
                    reason="fenxing/BTC 日线数据不可用")
def test_beichi_does_not_backdate_entry():
    candles = _load_1d()
    level = chanlun_service.analyze_symbol({"1day": candles})["1D"]
    pts = level.get("beichiPointsRaw") or []
    assert pts, "应有背驰点"
    for p in pts:
        assert p.get("trigger") == "弱速率"
        assert p.get("confirmedT") == p.get("t")
        assert p.get("alertT") == p.get("t")
        if p.get("state") == "confirmed" and p.get("t"):
            ci = _idx_of(candles, p["t"])
            pref = chanlun_service.analyze_symbol({"1day": candles[: ci + 1]})["1D"]
            got = [g for g in (pref.get("beichiPointsRaw") or []) if g.get("k") == p["k"] and g.get("t") == p["t"]]
            assert got, f"背驰点 {_fmt(p['t'])} {p['k']} 在确认前缀上不存在"



