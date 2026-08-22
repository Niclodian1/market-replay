import asyncio
from types import SimpleNamespace

from app.core.alert_engine import _alert_presentation, completed_indicator_candles, evaluate_alert


class CandleProvider:
    def __init__(self, candles):
        self.candles = candles

    async def get_time_series(self, symbol, timeframe, outputsize=200):
        return self.candles[-outputsize:]


def _candles(last_open=100.0):
    # 前一根大阴线跌破 MA30，最后一根大阳线（open 由 last_open 控制）上穿并收盘在高位
    closes = [100.0] * 119 + [85.0, 115.0]
    base = 1_577_836_800
    return [
        {
            "time": base + index * 86_400,
            "open": last_open if index == 120 else (85.0 if index == 119 else close),
            "high": max(close, last_open if index == 120 else close) * 1.05,
            "low": min(close, last_open if index == 120 else close) * 0.95,
            "close": close,
            "volume": 100.0,
        }
        for index, close in enumerate(closes)
    ]


def _alert():
    return SimpleNamespace(
        id=1,
        symbol="AAPL",
        timeframe="1day",
        condition_type="strategy_a_breakout",
        ma_period=30,
        threshold=0,
    )


def test_strategy_a_alert_requires_cross_and_three_percent_body():
    triggered, detail = asyncio.run(evaluate_alert(_alert(), CandleProvider(_candles())))
    assert triggered
    assert "MA30" in detail
    assert "上穿" in detail
    assert "买入方向" in detail

    # 最后一根不构成有效突破 → 不触发
    not_triggered, _ = asyncio.run(evaluate_alert(_alert(), CandleProvider(_candles(last_open=115.0))))
    assert not not_triggered


def test_indicator_alerts_drop_the_still_forming_candle():
    candles = [
        {"time": 10_000, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1},
        {"time": 13_600, "open": 1, "high": 2, "low": 0, "close": 1, "volume": 1},
    ]
    completed = completed_indicator_candles(candles, "1h", now_ts=17_199)
    assert [c["time"] for c in completed] == [10_000]
    assert completed[0]["is_closed"] is True


def test_legacy_alert_presentation_distinguishes_direction():
    assert _alert_presentation("ma_cross", "金叉（买入方向）") == ("🟢", 0x22C55E)
    assert _alert_presentation("ma_cross", "死叉（卖出方向）") == ("🔴", 0xEF4444)
    assert _alert_presentation("price_above", "突破价格") == ("🟢", 0x22C55E)
    assert _alert_presentation("price_below", "跌破价格") == ("🔴", 0xEF4444)
