"""信号探测器契约测试 —— strategy-spine 的信号层。

锁两件事:
1. detector.signal_array() 与底层 primitives 原语逐根一致(回测路径的等价性);
2. detector.detect() 把同一数组转成 SignalHit 事件(edge 流水线路径)。
"""
from app.core.signals import primitives
from app.core.signals.backtest_indicators import backtest_registry, get_backtest_signal
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
        "macd_divergence": primitives._signals_macd_divergence(closes, 5),
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
