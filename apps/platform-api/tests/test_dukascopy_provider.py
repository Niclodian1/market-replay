from app.providers.dukascopy import _decode_bucket, is_xauusd_symbol


def test_xau_symbol_aliases():
    assert is_xauusd_symbol("XAUUSD")
    assert is_xauusd_symbol("XAU/USD")
    assert is_xauusd_symbol("XAUUSD=X")
    assert not is_xauusd_symbol("GC=F")


def test_decode_delta_encoded_daily_bucket_and_drop_flat_bar():
    payload = {
        "timestamp": 1_700_000_000_000,
        "multiplier": 0.001,
        "open": 2000.0,
        "high": 2010.0,
        "low": 1990.0,
        "close": 2005.0,
        "shift": 86_400_000,
        "times": [0, 1, 1],
        "opens": [0, 1000, 1000],
        "highs": [0, 1000, 1000],
        "lows": [0, 1000, 1000],
        "closes": [0, 1000, 1000],
        "volumes": [10.0, 0.0, 12.0],
    }

    candles = _decode_bucket(payload)

    assert len(candles) == 2
    assert candles[0]["time"] == 1_700_000_000
    assert candles[0]["open"] == 2000.0
    # Deltas continue through the filtered flat candle.
    assert candles[1]["open"] == 2002.0
    assert candles[1]["time"] == 1_700_172_800
