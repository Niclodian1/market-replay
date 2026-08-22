from app.core.market_recommendations import (
    RecommendationCandidate,
    rank_recommendations,
)


CANDIDATES = (
    RecommendationCandidate("AAA", "Alpha", "stock", "us_equity", "美股", ("指数核心", "高流动性"), 1.0),
    RecommendationCandidate("BBB", "Beta", "stock", "us_equity", "美股", ("主题龙头", "AI"), 0.9),
    RecommendationCandidate("HL:BTC", "Bitcoin", "perpetual", "crypto", "加密", ("加密核心", "高流动性"), 1.0),
    RecommendationCandidate("GLD", "Gold", "etf", "cross_asset", "跨市场", ("黄金锚点", "风险状态"), 0.95),
    RecommendationCandidate("QQQ", "Nasdaq", "etf", "cross_asset", "跨市场", ("纳指锚点", "指数代理"), 0.9),
    RecommendationCandidate("CCC", "Gamma", "stock", "us_equity", "美股", ("行业龙头", "高流动性"), 0.85),
)


def _quote(symbol: str, price: float, volume: float, change: float) -> dict:
    return {
        "symbol": symbol,
        "name": symbol,
        "price": price,
        "volume": volume,
        "change_percent": change,
        "source": "test",
        "source_label": "Test",
    }


def test_recommendations_exclude_watchlist_and_cap_at_five():
    quotes = [
        _quote("AAA", 100, 1_000_000, 1),
        _quote("BBB", 20, 5_000_000, 3),
        {**_quote("HL:BTC", 60_000, 100, -2), "day_notional_volume": 2_000_000_000},
        _quote("GLD", 200, 2_000_000, 0.5),
        _quote("QQQ", 500, 3_000_000, 1.2),
        _quote("CCC", 50, 4_000_000, -1),
    ]

    result = rank_recommendations(
        quotes,
        ["AAA"],
        limit=99,
        candidates=CANDIDATES,
    )

    assert len(result) == 5
    assert all(item["symbol"] != "AAA" for item in result)


def test_recommendations_keep_multiple_markets_in_daily_report():
    quotes = [
        _quote("AAA", 100, 10_000_000, 1),
        _quote("BBB", 20, 10_000, 1),
        {**_quote("HL:BTC", 60_000, 1, 0.5), "day_notional_volume": 5_000},
        _quote("GLD", 200, 10, 0.2),
    ]

    result = rank_recommendations(
        quotes,
        [],
        limit=3,
        candidates=CANDIDATES,
    )

    assert {item["market"] for item in result} == {"us_equity", "crypto", "cross_asset"}


def test_recommendation_reason_uses_notional_and_daily_move():
    quotes = [
        _quote("AAA", 100, 10, 0.5),
        _quote("BBB", 20, 10_000_000, -3.25),
    ]

    result = rank_recommendations(
        quotes,
        [],
        limit=2,
        candidates=CANDIDATES[:2],
    )
    beta = next(item for item in result if item["symbol"] == "BBB")

    assert "候选池成交额靠前" in beta["reasons"]
    assert "收盘回撤 3.25%" in beta["reasons"]


def test_unavailable_or_zero_price_quotes_are_ignored():
    result = rank_recommendations(
        [None, _quote("AAA", 0, 1000, 2)],
        [],
        candidates=CANDIDATES,
    )

    assert result == []
