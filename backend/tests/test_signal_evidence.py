from app.core.signals.evidence import volume_evidence


def _candles(n: int) -> list[dict]:
    candles = []
    for i in range(n):
        candles.append({
            "time": 1_700_000_000 + i * 3600,
            "open": 101.0,
            "high": 102.0,
            "low": 100.0,
            "close": 101.0,
            "volume": 100.0,
        })
    return candles


def test_volume_ratios_exclude_current_bar():
    candles = _candles(11)
    for candle in candles[:-1]:
        candle["volume"] = 100.0
    candles[-1]["volume"] = 200.0

    result = volume_evidence(candles)

    assert result["windows"]["5"]["ratio_to_average"] == 2.0
    assert result["windows"]["10"]["ratio_to_average"] == 2.0
    assert result["strength"] == "high"
