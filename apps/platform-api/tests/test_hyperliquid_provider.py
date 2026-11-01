import asyncio

from app.core.hyperliquid_stream import HyperliquidStream
from app.providers.hyperliquid import (
    HyperliquidProvider,
    is_hyperliquid_symbol,
    to_canonical_symbol,
    to_hyperliquid_coin,
)


def _metadata():
    return (
        [{"name": "BTC", "maxLeverage": 40, "szDecimals": 5}],
        [{
            "midPx": "65000.5",
            "markPx": "65000.0",
            "oraclePx": "65010.0",
            "prevDayPx": "64000.0",
            "dayBaseVlm": "1000.5",
            "dayNtlVlm": "65000000.0",
            "funding": "0.0000125",
            "openInterest": "12000.0",
        }],
    )


def test_hyperliquid_symbol_round_trip():
    assert is_hyperliquid_symbol("HL:BTC")
    assert to_hyperliquid_coin("hl:btc") == "BTC"
    assert to_canonical_symbol("eth") == "HL:ETH"
    assert to_hyperliquid_coin("hl:xyz:aapl") == "xyz:AAPL"
    assert to_canonical_symbol("xyz:AAPL") == "HL:XYZ:AAPL"


def test_search_marks_hyperliquid_as_the_data_source(monkeypatch):
    provider = HyperliquidProvider()

    async def fake_all_metadata(ttl=300):
        return [{"universe": _metadata()[0]}]

    async def fake_dexes(ttl=300):
        return [{"name": "", "fullName": "Hyperliquid"}]

    async def fake_annotations(ttl=300):
        return {"BTC": {"category": "crypto"}}

    monkeypatch.setattr(provider, "get_all_perp_metadata", fake_all_metadata)
    monkeypatch.setattr(provider, "get_perp_dexes", fake_dexes)
    monkeypatch.setattr(provider, "get_concise_annotations", fake_annotations)
    result = asyncio.run(provider.search("BTC"))
    asyncio.run(provider.close())

    assert result == [{
        "symbol": "HL:BTC",
        "display_symbol": "BTC-PERP",
        "name": "BTC Perpetual",
        "type": "perpetual",
        "exchange": "Hyperliquid",
        "currency": "USDC",
        "source": "hyperliquid",
        "source_label": "Hyperliquid",
    }]


def test_search_includes_hip3_stocks_and_company_name_aliases(monkeypatch):
    provider = HyperliquidProvider()

    async def fake_all_metadata(ttl=300):
        return [{"collateralToken": 0, "universe": [
            {"name": "xyz:AAPL", "maxLeverage": 20},
            {"name": "xyz:CRCL", "maxLeverage": 10},
            {"name": "xyz:OLD", "maxLeverage": 3, "isDelisted": True},
        ]}]

    async def fake_dexes(ttl=300):
        return [
            {"name": "", "fullName": "Hyperliquid"},
            {"name": "xyz", "fullName": "XYZ"},
        ]

    async def fake_annotations(ttl=300):
        return {
            "XYZ:AAPL": {"category": "stocks", "displayName": "AAPL", "keywords": ["apple", "tech"]},
            "XYZ:CRCL": {"category": "stocks", "displayName": "CRCL", "keywords": ["circle", "crypto"]},
        }

    monkeypatch.setattr(provider, "get_all_perp_metadata", fake_all_metadata)
    monkeypatch.setattr(provider, "get_perp_dexes", fake_dexes)
    monkeypatch.setattr(provider, "get_concise_annotations", fake_annotations)
    result = asyncio.run(provider.search("Apple"))
    asyncio.run(provider.close())

    assert result == [{
        "symbol": "HL:XYZ:AAPL",
        "display_symbol": "AAPL-PERP",
        "name": "AAPL Perpetual (XYZ)",
        "type": "perpetual",
        "exchange": "Hyperliquid · XYZ",
        "currency": "USDC",
        "source": "hyperliquid",
        "source_label": "Hyperliquid · XYZ",
    }]


def test_plain_stock_tickers_resolve_to_active_hip3_symbols(monkeypatch):
    provider = HyperliquidProvider()
    calls = []

    async def fake_metadata(dex="", ttl=10):
        calls.append(dex)
        assert dex == "xyz"
        return ([
            {"name": "xyz:AAPL", "maxLeverage": 20},
            {"name": "xyz:CRCL", "maxLeverage": 10},
            {"name": "xyz:OLD", "isDelisted": True},
        ], [{}, {}, {}])

    async def fake_all_metadata(ttl=300):
        return [{"universe": [{"name": "other:MSFT"}]}]

    monkeypatch.setattr(provider, "get_metadata", fake_metadata)
    monkeypatch.setattr(provider, "get_all_perp_metadata", fake_all_metadata)

    async def run():
        first = await provider.resolve_stock_aliases(["AAPL", "CRCL", "OLD", "^GSPC", "BTC-USD"])
        second = await provider.resolve_stock_aliases(["AAPL", "MSFT"])
        await provider.close()
        return first, second

    first, second = asyncio.run(run())

    assert first == {"AAPL": "HL:XYZ:AAPL", "CRCL": "HL:XYZ:CRCL"}
    assert second == {"AAPL": "HL:XYZ:AAPL", "MSFT": "HL:OTHER:MSFT"}
    assert calls == ["xyz", "xyz"]


def test_quote_exposes_derivatives_metrics(monkeypatch):
    provider = HyperliquidProvider()

    async def fake_metadata(dex="", ttl=10):
        assert dex == ""
        return _metadata()

    monkeypatch.setattr(provider, "get_metadata", fake_metadata)
    quote = asyncio.run(provider.get_quote("HL:BTC"))
    asyncio.run(provider.close())

    assert quote["price"] == 65000.5
    assert quote["funding_rate"] == 0.0000125
    assert quote["open_interest"] == 12000.0
    assert quote["day_notional_volume"] == 65000000.0
    assert quote["source"] == "hyperliquid"


def test_candles_are_normalized_and_respect_before_cursor(monkeypatch):
    provider = HyperliquidProvider()

    async def fake_metadata(dex="", ttl=10):
        assert dex == ""
        return _metadata()

    async def fake_post(payload):
        return [
            {"t": 1_700_000_000_000, "o": "10", "h": "12", "l": "9", "c": "11", "v": "3"},
            {"t": 1_700_003_600_000, "o": "11", "h": "13", "l": "10", "c": "12", "v": "4"},
        ]

    monkeypatch.setattr(provider, "_post", fake_post)
    monkeypatch.setattr(provider, "get_metadata", fake_metadata)
    candles = asyncio.run(provider.get_time_series("HL:BTC", "1h", 10, before=1_700_003_600))
    asyncio.run(provider.close())

    assert candles == [{
        "time": 1_700_000_000,
        "open": 10.0,
        "high": 12.0,
        "low": 9.0,
        "close": 11.0,
        "volume": 3.0,
    }]


def test_hip3_quote_and_candles_use_dex_prefix(monkeypatch):
    provider = HyperliquidProvider()
    requests = []
    hip3_meta = (
        [{"name": "xyz:AAPL", "maxLeverage": 20, "szDecimals": 3}],
        [{
            "midPx": "212.5",
            "markPx": "212.4",
            "oraclePx": "212.3",
            "prevDayPx": "210.0",
            "dayBaseVlm": "1000",
            "dayNtlVlm": "212500",
            "funding": "0.00001",
            "openInterest": "500",
        }],
    )

    async def fake_metadata(dex="", ttl=10):
        assert dex == "xyz"
        return hip3_meta

    async def fake_post(payload):
        requests.append(payload)
        return [{"t": 1_700_000_000_000, "o": "210", "h": "213", "l": "209", "c": "212", "v": "5"}]

    monkeypatch.setattr(provider, "get_metadata", fake_metadata)
    monkeypatch.setattr(provider, "_post", fake_post)
    quote = asyncio.run(provider.get_quote("HL:XYZ:AAPL"))
    candles = asyncio.run(provider.get_time_series("HL:XYZ:AAPL", "1h", 10))
    asyncio.run(provider.close())

    assert quote["price"] == 212.5
    assert quote["source_label"] == "Hyperliquid · XYZ"
    assert requests[0]["req"]["coin"] == "xyz:AAPL"
    assert candles[0]["close"] == 212.0


def test_concurrent_quote_and_candles_share_metadata_request(monkeypatch):
    provider = HyperliquidProvider()
    requests = []

    async def fake_post(payload):
        requests.append(payload)
        if payload.get("type") == "metaAndAssetCtxs":
            await asyncio.sleep(0.01)
            universe, contexts = _metadata()
            return [{"universe": universe}, contexts]
        return [{"t": 1_700_000_000_000, "o": "10", "h": "12", "l": "9", "c": "11", "v": "3"}]

    monkeypatch.setattr(provider, "_post", fake_post)

    async def run():
        quote, candles = await asyncio.gather(
            provider.get_quote("HL:BTC"),
            provider.get_time_series("HL:BTC", "1day", 500),
        )
        await provider.close()
        return quote, candles

    quote, candles = asyncio.run(run())

    assert quote["price"] == 65000.5
    assert candles[0]["close"] == 11.0
    assert sum(payload.get("type") == "metaAndAssetCtxs" for payload in requests) == 1


def test_stream_replays_latest_snapshot_to_new_subscribers():
    stream = HyperliquidStream()
    stream._latest = {"HL:BTC": 65000.0}
    queue = stream.subscribe()
    payload = queue.get_nowait()
    stream.unsubscribe(queue)

    assert payload["type"] == "allMids"
    assert payload["mids"] == {"HL:BTC": 65000.0}


def test_stream_subscribes_to_every_hip3_dex(monkeypatch):
    stream = HyperliquidStream()

    class FakeProvider:
        async def get_perp_dex_names(self):
            return ["xyz", "flx"]

    monkeypatch.setattr("app.core.hyperliquid_stream.get_hyperliquid_provider", lambda: FakeProvider())
    subscriptions = asyncio.run(stream._all_mids_subscriptions())

    assert subscriptions == [
        {"type": "allMids"},
        {"type": "allMids", "dex": "xyz"},
        {"type": "allMids", "dex": "flx"},
    ]
