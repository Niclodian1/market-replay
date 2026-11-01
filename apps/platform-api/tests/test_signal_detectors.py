"""信号探测器契约测试 —— strategy-spine 的信号层。

锁两件事:
1. detector.signal_array() 与底层 primitives 原语逐根一致(回测路径的等价性);
2. detector.detect() 把同一数组转成 SignalHit 事件（策略探测路径）。
"""
from app.core.signals import primitives
from app.core.signals.backtest_indicators import backtest_registry, get_backtest_signal
from app.core.divergence_detectors import macd_extreme_divergence_array
from tests.golden_helpers import make_candles


def test_registry_has_six_signals():
    assert set(backtest_registry.names) == {
        "ma_cross", "rsi", "macd", "ma_breakout", "bollinger", "macd_divergence",
    }


def test_signal_array_matches_primitive():
    candles = make_candles()
    closes = [c["close"] for c in candles]
    cases = {
        "ma_cross": primitives._signals_ma_cross(closes, 10, 30),
        "rsi": primitives._signals_rsi(closes, 30.0, 70.0),
        "macd": primitives._signals_macd(closes),
        "ma_breakout": primitives._signals_ma_breakout(closes, 20),
        "bollinger": primitives._signals_bollinger(closes, 20, 2.0),
        "macd_divergence": macd_extreme_divergence_array(candles),
    }
    for name, expected in cases.items():
        got = get_backtest_signal(name).signal_array(candles, {})
        assert got == expected, f"{name} signal_array 与原语不一致"


def test_detect_emits_hits_at_nonzero_bars():
    candles = make_candles()
    det = get_backtest_signal("ma_cross")
    arr = det.signal_array(candles, {})
    hits = det.detect(candles, "1h")
    assert len(hits) == sum(1 for s in arr if s != 0)
    for h in hits:
        assert h.name == "ma_cross"
        assert h.direction in ("long", "short")


def test_ma_breakout_hits_include_structured_volume_evidence():
    candles = make_candles()
    hits = get_backtest_signal("ma_breakout").detect(candles, "1h")
    assert hits
    payload = hits[-1].payload
    assert payload["schema_version"] == 1
    assert payload["signal_type"] == "ma_breakout"
    assert payload["state"] == "confirmed"
    assert payload["bar_closed"] is True
    assert set(payload["evidence"]["volume"]["windows"]) == {"5", "10"}
    assert payload["evidence"]["volume"]["windows"]["10"]["ratio_to_average"] is not None
