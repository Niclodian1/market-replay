import asyncio

from app.api import market


class FakeHyperliquidProvider:
    async def get_quote(self, symbol):
        assert symbol == "HL:XYZ:AAPL"
        return {
            "symbol": symbol,
            "display_symbol": "AAPL-PERP",
            "name": "AAPL Perpetual (XYZ)",
            "price": 210.0,
            "change": 2.0,
            "change_percent": 0.96,
            "volume": 100.0,
            "source": "hyperliquid",
            "source_label": "Hyperliquid · XYZ",
            "timestamp": "2026-07-16T00:00:00+00:00",
        }

    async def get_time_series(self, symbol, timeframe, outputsize, before=None):
        assert (symbol, timeframe, outputsize, before) == ("HL:XYZ:AAPL", "1day", 500, None)
        return [{
            "time": 1_700_000_000,
            "open": 209.0,
            "high": 212.0,
            "low": 208.0,
            "close": 210.0,
            "volume": 10.0,
        }]


class FakeYahooProvider:
    async def get_quotes_batch(self, symbols):
        assert symbols == ["AAPL"]
        return [{
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "price": 211.0,
            "change": 1.0,
            "change_percent": 0.48,
            "volume": 200.0,
            "timestamp": "2026-07-16T00:00:00+00:00",
        }]

    async def get_time_series(self, symbol, timeframe, outputsize, before=None):
        assert (symbol, timeframe, outputsize, before) == ("AAPL", "1day", 500, None)
        return [{
            "time": 1_700_000_000,
            "open": 210.0,
            "high": 212.0,
            "low": 209.0,
            "close": 211.0,
            "volume": 20.0,
        }]


class FakeBinanceFuturesProvider:
    async def get_quote(self, symbol):
        assert symbol == "BINANCE:AAPLUSDT"
        return {
            "symbol": symbol,
            "provider_symbol": "AAPLUSDT",
            "display_symbol": "AAPL-PERP",
            "name": "AAPL 美股永续",
            "price": 210.0,
            "change": 2.0,
            "change_percent": 0.96,
            "volume": 100.0,
            "source": "binance_futures",
            "source_label": "Binance TradFi Perpetual",
            "timestamp": "2026-07-21T00:00:00+00:00",
        }

    async def get_time_series(self, symbol, timeframe, outputsize, before=None):
        assert (symbol, timeframe, outputsize, before) == (
            "BINANCE:AAPLUSDT", "15min", 500, None,
        )
        return [{
            "time": 1_700_000_000,
            "open": 209.0,
            "high": 212.0,
            "low": 208.0,
            "close": 210.0,
            "volume": 10.0,
        }]

def test_plain_stock_auto_source_stays_on_stock_market(monkeypatch):
    async def unexpected_alias_lookup(_symbols):
        raise AssertionError("auto stock requests must not become perpetuals")

    yahoo = FakeYahooProvider()
    monkeypatch.setattr(market, "_resolve_hyperliquid_stock_aliases", unexpected_alias_lookup)
    monkeypatch.setattr(market, "get_provider", lambda: yahoo)

    quote = asyncio.run(market.get_quote("AAPL", "auto"))
    candles = asyncio.run(market.get_candles("AAPL", "1day", 500, None, "auto"))

    assert quote["symbol"] == "AAPL"
    assert quote["source"] == "yahoo"
    assert candles["symbol"] == "AAPL"
    assert candles["source"] == "yahoo"


def test_explicit_yahoo_source_bypasses_hyperliquid(monkeypatch):
    async def unexpected_alias_lookup(_symbols):
        raise AssertionError("Yahoo requests must not query Hyperliquid aliases")

    yahoo = FakeYahooProvider()
    monkeypatch.setattr(market, "_resolve_hyperliquid_stock_aliases", unexpected_alias_lookup)
    monkeypatch.setattr(market, "get_provider", lambda: yahoo)

    quote = asyncio.run(market.get_quote("AAPL", "yahoo"))
    candles = asyncio.run(market.get_candles("AAPL", "1day", 500, None, "yahoo"))

    assert quote["symbol"] == "AAPL"
    assert quote["display_symbol"] == "AAPL"
    assert quote["source"] == "yahoo"
    assert "provider_symbol" not in quote
    assert candles["symbol"] == "AAPL"
    assert candles["source"] == "yahoo"
    assert "provider_symbol" not in candles


def test_explicit_hyperliquid_source_does_not_fall_back_to_yahoo(monkeypatch):
    async def fake_aliases(symbols):
        assert symbols == ["AAPL"]
        return {"AAPL": "HL:XYZ:AAPL"}

    provider = FakeHyperliquidProvider()
    monkeypatch.setattr(market, "_resolve_hyperliquid_stock_aliases", fake_aliases)
    monkeypatch.setattr(market, "get_hyperliquid_provider", lambda: provider)
    monkeypatch.setattr(
        market,
        "get_provider",
        lambda: (_ for _ in ()).throw(AssertionError("must not use Yahoo")),
    )

    quote = asyncio.run(market.get_quote("AAPL", "hyperliquid"))
    candles = asyncio.run(market.get_candles("AAPL", "1day", 500, None, "hyperliquid"))

    assert quote["source"] == "hyperliquid"
    assert candles["source"] == "hyperliquid"


def test_binance_tradfi_perpetual_uses_explicit_product_identity(monkeypatch):
    provider = FakeBinanceFuturesProvider()
    monkeypatch.setattr(market, "get_binance_futures_provider", lambda: provider)

    quote = asyncio.run(market.get_quote("BINANCE:AAPLUSDT", "auto"))
    candles = asyncio.run(
        market.get_candles("BINANCE:AAPLUSDT", "15min", 500, None, "auto")
    )

    assert quote["source"] == "binance_futures"
    assert quote["symbol"] == "BINANCE:AAPLUSDT"
    assert candles["source"] == "binance_futures"
    assert candles["provider_symbol"] == "AAPLUSDT"


def test_capabilities_expose_15m_binance_spot_and_perpetuals():
    payload = asyncio.run(market.get_market_capabilities())
    sources = {item["id"]: item for item in payload["sources"]}

    assert "15min" in sources["binance_spot"]["timeframes"]
    assert "15min" in sources["binance_futures"]["timeframes"]
    assert "equity_perpetual" in sources["binance_futures"]["asset_types"]
