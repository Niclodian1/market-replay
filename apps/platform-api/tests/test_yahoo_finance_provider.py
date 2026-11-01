import asyncio

import pandas as pd

from app.providers import yahoo_finance
from app.providers.yahoo_finance import YahooFinanceProvider, _Cache, _lookback_days


def test_cache_can_serve_recently_expired_quote(monkeypatch):
    now = 1_000.0
    monkeypatch.setattr(yahoo_finance.time, "time", lambda: now)
    cache = _Cache()
    cache.set("quote", {"price": 123})

    now = 1_061.0
    assert cache.get("quote", ttl=60) is None
    assert cache.get_stale("quote", max_age=3600) == {"price": 123}


def test_batch_quotes_passes_short_upstream_timeout(monkeypatch):
    captured = {}

    def fake_download(**kwargs):
        captured.update(kwargs)
        columns = pd.MultiIndex.from_tuples([
            ("FAST-TEST", "Close"),
            ("FAST-TEST", "Volume"),
        ])
        return pd.DataFrame([[100.0, 10], [105.0, 12]], columns=columns)

    monkeypatch.setattr(yahoo_finance.yf, "download", fake_download)
    provider = YahooFinanceProvider()
    quotes = asyncio.run(provider.get_quotes_batch(["FAST-TEST"]))

    assert captured["timeout"] == 4
    assert quotes[0]["price"] == 105.0
    assert quotes[0]["change_percent"] == 5.0


def test_chart_history_uses_requested_bar_range_and_timeout(monkeypatch):
    captured = {}

    class FakeTicker:
        def __init__(self, symbol):
            assert symbol == "RANGE-TEST"

        def history(self, **kwargs):
            captured.update(kwargs)
            index = pd.date_range("2026-07-14", periods=2, tz="UTC")
            return pd.DataFrame({
                "Open": [100.0, 101.0],
                "High": [102.0, 103.0],
                "Low": [99.0, 100.0],
                "Close": [101.0, 102.0],
                "Volume": [10, 12],
            }, index=index)

    monkeypatch.setattr(yahoo_finance.yf, "Ticker", FakeTicker)
    provider = YahooFinanceProvider()
    candles = asyncio.run(provider.get_time_series("RANGE-TEST", "1day", 500))

    assert len(candles) == 2
    assert captured["timeout"] == 6
    assert (captured["end"] - captured["start"]).days == _lookback_days("1day", 500)
    assert _lookback_days("1day", 500) < 1_000
