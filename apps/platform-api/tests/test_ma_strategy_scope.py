from app.api.ma_strategies_unified_api import (
    AB_OPTION_KEYS,
    LEVERAGES,
    PRODUCT_SYMBOLS,
    STRATEGIES,
    TIMEFRAMES,
    get_overview,
)
from scripts.backtests.backtest_ma_strategies import run_breakout


def test_phase_one_strategy_scope_is_fixed():
    assert list(PRODUCT_SYMBOLS) == ["BTC-USD", "AAPL", "XAUUSD", "600519.SS"]
    assert AB_OPTION_KEYS == ("30", "60", "99", "120")
    assert STRATEGIES == ("A",)
    assert TIMEFRAMES == ("1day",)
    assert LEVERAGES == ("none",)


def test_strategy_a_keeps_multiple_positions_independent():
    closes = [100.0, 90.0, 110.0, 104.0, 115.0, 120.0]
    opens = [100.0, 90.0, 100.0, 104.0, 105.0, 120.0]
    lows = [99.0, 89.0, 80.0, 100.0, 90.0, 119.0]
    dates = list(range(len(closes)))

    result = run_breakout(closes, lows, opens, dates, 2)

    assert result is not None
    assert result["n_trades"] == 2
    assert [trade[0] for trade in result["trade_log"]] == [2, 4]
    assert all(trade[3] == "强制平仓" for trade in result["trade_log"])


def test_strategy_overview_exposes_both_ranking_dimensions():
    overview = get_overview()

    assert [item["symbol"] for item in overview["symbols"]] == ["BTC-USD", "AAPL", "XAUUSD", "600519.SS"]
    assert overview["strategies"][0]["parameters"] == ["30", "60", "99", "120"]
    assert len(overview["ranked_symbols"]) == 4
    assert overview["ranked_symbols"] == sorted(
        overview["ranked_symbols"],
        key=lambda item: (item["sample_count"], item["positive_variants"], item["weighted_expectancy"]),
        reverse=True,
    )
    for item in overview["symbols"]:
        assert len(item["variants"]) == 4
        assert item["sample_count"] == sum(variant["stats"]["n_trades"] for variant in item["variants"])
