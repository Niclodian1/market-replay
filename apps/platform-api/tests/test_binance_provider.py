import asyncio

from app.providers.binance import (
    BinanceFuturesProvider,
    BinanceSpotProvider,
    is_binance_futures_symbol,
    to_binance_futures_symbol,
    to_binance_spot_symbol,
)


def _kline(open_ms: int, close_ms: int, close: str = "11"):
    return [
        open_ms, "10", "12", "9", close, "3", close_ms,
        "33", 7, "1.5", "16.5", "0",
    ]


def test_binance_product_symbols_are_unambiguous():
    assert to_binance_spot_symbol("BTC-USD") == "BTCUSDT"
    assert to_binance_spot_symbol("ETH/USDT") == "ETHUSDT"
    assert is_binance_futures_symbol("binance:aaplusdt")
    assert to_binance_futures_symbol("BINANCE:PERP:BTC-USDT") == "BTCUSDT"


def test_spot_15m_candles_keep_binance_volume_evidence(monkeypatch):
    provider = BinanceSpotProvider()
    requests = []

    async def fake_request(path, params=None):
        requests.append((path, params))
        return [_kline(1_700_000_000_000, 1_700_000_899_999)]

    monkeypatch.setattr(provider, "_request", fake_request)
    candles = asyncio.run(provider.get_time_series("BTC-USD", "15min", 1, before=1_800_000_000))

    assert requests[0][0] == "/api/v3/klines"
    assert requests[0][1]["symbol"] == "BTCUSDT"
    assert requests[0][1]["interval"] == "15m"
    assert candles[0]["volume"] == 3.0
    assert candles[0]["quote_volume"] == 33.0
    assert candles[0]["trade_count"] == 7
    assert candles[0]["is_closed"] is True


def test_futures_search_marks_tradfi_as_equity_perpetual(monkeypatch):
    provider = BinanceFuturesProvider()

    async def fake_info(ttl=300):
        return {
            "AAPLUSDT": {
                "symbol": "AAPLUSDT",
                "baseAsset": "AAPL",
                "quoteAsset": "USDT",
                "contractType": "TRADIFI_PERPETUAL",
            },
            "BTCUSDT": {
                "symbol": "BTCUSDT",
                "baseAsset": "BTC",
                "quoteAsset": "USDT",
                "contractType": "PERPETUAL",
            },
        }

    monkeypatch.setattr(provider, "get_exchange_info", fake_info)
    result = asyncio.run(provider.search("Apple", aliases=["AAPL"]))

    assert result == [{
        "symbol": "BINANCE:AAPLUSDT",
        "display_symbol": "AAPL-PERP",
        "name": "AAPL 美股永续",
        "type": "equity_perpetual",
        "exchange": "Binance TradFi",
        "currency": "USDT",
        "source": "binance_futures",
        "source_label": "Binance TradFi Perpetual",
    }]


def test_futures_quote_exposes_mark_funding_and_open_interest(monkeypatch):
    provider = BinanceFuturesProvider()

    async def fake_request(path, params=None):
        if path.endswith("ticker/24hr"):
            return {
                "lastPrice": "210", "priceChange": "2", "priceChangePercent": "0.96",
                "volume": "100", "quoteVolume": "21000",
            }
        if path.endswith("premiumIndex"):
            return {"markPrice": "210.5", "indexPrice": "210.2", "lastFundingRate": "0.0001"}
        if path.endswith("openInterest"):
            return {"openInterest": "500"}
        raise AssertionError(path)

    async def fake_info(ttl=300):
        return {"AAPLUSDT": {
            "baseAsset": "AAPL", "contractType": "TRADIFI_PERPETUAL",
        }}

    monkeypatch.setattr(provider, "_request", fake_request)
    monkeypatch.setattr(provider, "get_exchange_info", fake_info)
    quote = asyncio.run(provider.get_quote("BINANCE:AAPLUSDT"))

    assert quote["source"] == "binance_futures"
    assert quote["mark_price"] == 210.5
    assert quote["funding_rate"] == 0.0001
    assert quote["open_interest"] == 500.0
    assert quote["open_interest_value"] == 105250.0
