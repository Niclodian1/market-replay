"""柱极值背离告警（macd_divergence 新逻辑）测试。"""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from app.core.alert_engine import evaluate_alert
from app.core.divergence_detectors import macd_extreme_divergence_events

_PARQUET = Path(__file__).resolve().parents[2] / "data" / "crypto" / "BTC_USD_1day.parquet"


def _btc_candles():
    df = pd.read_parquet(_PARQUET)[["time", "open", "high", "low", "close", "volume"]]
    df = df.dropna().sort_values("time")
    return df.to_dict("records")


class CandleProvider:
    def __init__(self, candles):
        self.candles = candles

    async def get_time_series(self, symbol, timeframe, outputsize=200):
        return self.candles[-outputsize:]


def _alert():
    return SimpleNamespace(
        id=1,
        symbol="BTC-USD",
        timeframe="1day",
        condition_type="macd_divergence",
        threshold=0,
        ma_period=None,
    )


def test_detector_emits_bidirectionally_with_tiers():
    events = macd_extreme_divergence_events(_btc_candles())
    sides = {e["side"] for e in events}
    tiers = {e["strength_tier"] for e in events}
    assert {"buy", "sell"} <= sides
    assert {"强", "中", "弱"} <= tiers
    for e in events:
        assert 0 < e["strength"] <= 1
        assert e["state"] in ("confirmed", "pending")
        assert e["alertT"] is not None
        assert e["prev_extreme"] is not None


def test_alert_triggers_on_latest_bar_divergence():
    from datetime import datetime, timezone

    def _day(t):
        return datetime.fromtimestamp(int(t), tz=timezone.utc).strftime("%Y-%m-%d")

    candles = _btc_candles()
    idx = next(i for i, c in enumerate(candles) if _day(c["time"]) == "2026-06-29")
    trunc = candles[: idx + 1]  # 最新 bar = 06-29（存在多头背离事件）
    triggered, detail = asyncio.run(evaluate_alert(_alert(), CandleProvider(trunc)))
    assert triggered
    assert "多头背离" in detail
    assert "强度" in detail
    assert "参照柱极值" in detail
    assert "首衰告警" in detail
    assert "买入方向" in detail


def test_alert_does_not_retrigger_historical_divergence():
    # 完整数据时最新 bar（07-17）无新背离 → 不应触发（避免历史重复告警）
    triggered, detail = asyncio.run(evaluate_alert(_alert(), CandleProvider(_btc_candles())))
    assert not triggered
    assert detail == ""
