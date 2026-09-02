from pathlib import Path
from unittest.mock import patch

from app.core.signals.monitor_signals import (
    detect_ma_events,
    detect_macd_events,
    detect_structure_events,
)


def _flat_candles(count: int = 130) -> list[dict]:
    return [{
        "time": 1_700_000_000 + i * 3600,
        "open": 100.0,
        "high": 100.5,
        "low": 99.5,
        "close": 100.0,
        "volume": 100.0,
    } for i in range(count)]


def test_ma_breakout_uses_atr_and_volume_evidence():
    candles = _flat_candles()
    candles[-2].update(open=100.0, high=100.2, low=98.7, close=99.0)
    candles[-1].update(open=99.0, high=102.2, low=98.8, close=102.0, volume=200.0)

    events = detect_ma_events(candles, "1h")

    assert events, "默认周期 (30,60,99,120) 应产生至少一个突破信号"
    periods = {event["payload"]["parameters"]["period"] for event in events}
    assert periods <= {30, 60, 99, 120}, f"应只包含 30/60/99/120，实际 {periods}"
    ma30 = next(event for event in events if event["payload"]["parameters"]["period"] == 30)
    assert ma30["event_type"] == "ma_breakout"
    assert ma30["direction"] == "long"
    assert ma30["payload"]["evidence"]["price"]["body_atr"] > 0.5
    assert ma30["payload"]["evidence"]["volume"]["windows"]["10"]["ratio_to_average"] == 2.0


def test_ma_breakout_includes_slope_in_evidence_and_summary():
    candles = _flat_candles()
    candles[-2].update(open=100.0, high=100.2, low=98.7, close=99.0)
    candles[-1].update(open=99.0, high=102.2, low=98.8, close=102.0, volume=200.0)

    events = detect_ma_events(candles, "1h", periods=(30,))
    assert events, "应产生 MA30 突破信号"
    ev = events[0]
    slope = ev["payload"]["evidence"]["ma"]["slope_atr_per_bar"]
    assert slope is not None, "MA 斜率应存在"
    assert "斜率" in ev["summary"]


def _ma30_slope_sign(candles: list[dict]) -> float:
    from app.core.indicators import _sma
    from app.core.signals.evidence import _ma_slope

    closes = [float(c["close"]) for c in candles]
    ma = _sma(closes, 30)
    return _ma_slope(ma, len(candles) - 1, window=30)


def test_ma_breakout_marks_inconsistent_slope_when_against_direction():
    # 看多突破但 MA 斜率向下：summary 应标记「斜率指标不一致」
    candles = _flat_candles()
    # 前段上行建立向上趋势，随后持续下行制造向下 MA 斜率
    for i in range(80):
        candles[i]["close"] = 100.0 + i * 0.1
        candles[i]["open"] = candles[i]["close"] - 0.05
        candles[i]["high"] = candles[i]["close"] + 0.3
        candles[i]["low"] = candles[i]["close"] - 0.3
    for i in range(80, 129):
        candles[i]["close"] = 108.0 - (i - 80) * 0.08
        candles[i]["open"] = candles[i]["close"] + 0.05
        candles[i]["high"] = candles[i]["close"] + 0.3
        candles[i]["low"] = candles[i]["close"] - 0.3
    # 最后一根阳线上穿当前 MA
    candles[-1].update(open=104.0, high=105.8, low=103.9, close=105.5, volume=200.0)

    assert _ma30_slope_sign(candles) < 0, "前置条件：MA30 斜率应向下"

    events = detect_ma_events(candles, "1h", periods=(30,))
    longs = [e for e in events if e["direction"] == "long"]
    assert longs, "应产生看多突破信号"
    assert any("斜率指标不一致" in e["summary"] for e in longs)


def test_ma_breakout_consistent_slope_no_flag():
    # 看多突破且 MA 斜率向上（≥0）：不出现「斜率指标不一致」标记
    candles = _flat_candles()
    for i in range(129):
        candles[i].update(open=99.9, high=100.4, low=99.4, close=100.0)
    candles[-1].update(open=100.0, high=102.5, low=99.9, close=102.0, volume=200.0)

    assert _ma30_slope_sign(candles) >= 0, "前置条件：MA30 斜率应向上"

    events = detect_ma_events(candles, "1h", periods=(30,))
    longs = [e for e in events if e["direction"] == "long"]
    assert longs, "应产生看多突破信号"
    assert all("斜率指标不一致" not in e["summary"] for e in longs)


def test_alert_engine_ma_slope_text_consistency_flag():
    from app.core.alert_engine import _ma_slope_text
    from app.core.indicators import _sma

    closes_up = [100.0 + i * 0.1 for i in range(40)]
    closes_down = [110.0 - i * 0.1 for i in range(40)]

    up_ma = _sma(closes_up, 20)
    down_ma = _sma(closes_down, 20)

    # 斜率向下 + long 方向：标记不一致
    assert "斜率指标不一致" in _ma_slope_text(down_ma, 20, direction="long")
    # 斜率向下 + short 方向：一致
    assert "斜率指标不一致" not in _ma_slope_text(down_ma, 20, direction="short")
    # 斜率向上 + short 方向：标记不一致
    assert "斜率指标不一致" in _ma_slope_text(up_ma, 20, direction="short")
    # 斜率向上 + long 方向：一致
    assert "斜率指标不一致" not in _ma_slope_text(up_ma, 20, direction="long")


def test_ma_breakout_only_no_retest():
    # 突破后回踩：只应记录 breakout，不再产生 retest
    candles = _flat_candles()
    candles[-5].update(open=100.0, high=100.1, low=98.8, close=99.0)
    candles[-4].update(open=99.0, high=102.2, low=98.9, close=102.0)
    candles[-3].update(open=102.0, high=102.5, low=101.5, close=102.1)
    candles[-2].update(open=102.1, high=102.3, low=101.4, close=101.8)
    candles[-1].update(open=100.1, high=101.8, low=99.9, close=101.5)

    events = detect_ma_events(candles, "1h", periods=(30,))

    assert not any(event["event_type"] == "ma_retest" for event in events)
    assert all(event["event_type"] == "ma_breakout" for event in events)


def test_macd_extreme_divergence_detector_on_real_data():
    import pandas as pd

    df = pd.read_parquet(
        Path(__file__).resolve().parents[2] / "data" / "crypto" / "BTC_USD_1day.parquet"
    )[["time", "open", "high", "low", "close", "volume"]].dropna().sort_values("time")
    candles = df.to_dict("records")

    events = detect_macd_events(candles, "1day")

    # 柱极值背离只报告最新已完成 bar 上的事件
    latest_ts = candles[-1]["time"]
    assert all(int(ev["payload"]["evidence"]["t"]) == latest_ts for ev in events)
    for ev in events:
        assert ev["event_type"] == "macd_extreme_divergence"
        assert ev["direction"] in {"long", "short"}
        assert ev["payload"]["evidence"]["strength"] > 0
        assert ev["payload"]["evidence"]["strength_tier"] in {"强", "中", "弱"}


def test_structure_breakout_requires_close_and_labels_convergence():
    candles = _flat_candles(60)
    candles[-2].update(open=104.5, high=105.5, low=104.0, close=105.0)
    candles[-1].update(open=105.0, high=108.5, low=104.8, close=108.0, volume=180.0)
    upper = [(20, 110.0), (30, 109.0), (40, 108.0)]
    lower = [(20, 90.0), (30, 92.0), (40, 94.0)]

    with (
        patch("app.core.signals.monitor_signals._swings", return_value=(upper, lower)),
        patch("app.core.signals.monitor_signals._atr", return_value=[1.0] * len(candles)),
    ):
        events = detect_structure_events(candles, "1h")

    assert len(events) == 1
    assert events[0]["event_type"] == "convergence_breakout"
    assert events[0]["direction"] == "long"
    assert events[0]["payload"]["evidence"]["structure"]["converging"] is True
