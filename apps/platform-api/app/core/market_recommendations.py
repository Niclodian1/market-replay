from __future__ import annotations

from dataclasses import dataclass
from math import log1p
from typing import Iterable, Optional


@dataclass(frozen=True)
class RecommendationCandidate:
    symbol: str
    name: str
    asset_type: str
    market: str
    market_label: str
    tags: tuple[str, ...]
    leadership_weight: float


# This is the deep-scan candidate catalog, not a claim that the symbols below
# are always trading opportunities. The daily report explains why a symbol is
# worth watching; a symbol only enters the user's strategy scan after they add
# it to the watchlist.
RECOMMENDATION_CANDIDATES: tuple[RecommendationCandidate, ...] = (
    RecommendationCandidate("AAPL", "Apple", "stock", "us_equity", "美股", ("标普/纳指核心", "高流动性"), 1.00),
    RecommendationCandidate("MSFT", "Microsoft", "stock", "us_equity", "美股", ("标普/纳指核心", "AI平台"), 1.00),
    RecommendationCandidate("NVDA", "NVIDIA", "stock", "us_equity", "美股", ("标普/纳指核心", "AI算力"), 1.00),
    RecommendationCandidate("AMZN", "Amazon", "stock", "us_equity", "美股", ("标普/纳指核心", "云计算"), 0.96),
    RecommendationCandidate("META", "Meta Platforms", "stock", "us_equity", "美股", ("标普/纳指核心", "AI应用"), 0.96),
    RecommendationCandidate("GOOGL", "Alphabet", "stock", "us_equity", "美股", ("标普/纳指核心", "AI平台"), 0.96),
    RecommendationCandidate("AVGO", "Broadcom", "stock", "us_equity", "美股", ("半导体龙头", "AI算力"), 0.93),
    RecommendationCandidate("AMD", "AMD", "stock", "us_equity", "美股", ("半导体龙头", "AI算力"), 0.90),
    RecommendationCandidate("MU", "Micron", "stock", "us_equity", "美股", ("存储周期", "高成交活跃度"), 0.88),
    RecommendationCandidate("TSLA", "Tesla", "stock", "us_equity", "美股", ("AI运输", "高成交活跃度"), 0.90),
    RecommendationCandidate("CRCL", "Circle Internet Group", "stock", "us_equity", "美股", ("稳定币", "主题龙头"), 0.92),
    RecommendationCandidate("TEM", "Tempus AI", "stock", "us_equity", "美股", ("AI医疗", "主题龙头"), 0.88),
    RecommendationCandidate("HL:BTC", "Bitcoin Perpetual", "perpetual", "crypto", "加密", ("加密核心", "高流动性"), 1.00),
    RecommendationCandidate("HL:ETH", "Ethereum Perpetual", "perpetual", "crypto", "加密", ("加密核心", "高流动性"), 0.98),
    RecommendationCandidate("HL:SOL", "Solana Perpetual", "perpetual", "crypto", "加密", ("加密龙头", "高成交活跃度"), 0.92),
    RecommendationCandidate("SPY", "SPDR S&P 500 ETF", "etf", "cross_asset", "跨市场", ("标普500锚点", "指数代理"), 0.98),
    RecommendationCandidate("QQQ", "Invesco QQQ", "etf", "cross_asset", "跨市场", ("纳指100锚点", "指数代理"), 0.98),
    RecommendationCandidate("GLD", "SPDR Gold Shares", "etf", "cross_asset", "跨市场", ("黄金锚点", "风险状态"), 0.94),
)


def _percentile_ranks(values: list[float]) -> list[float]:
    if not values:
        return []
    if len(values) == 1:
        return [1.0]
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    denominator = len(values) - 1
    for rank, index in enumerate(order):
        ranks[index] = rank / denominator
    return ranks


def _market_percentile_ranks(
    available: list[tuple[RecommendationCandidate, dict, float]],
    value_getter,
) -> list[float]:
    ranks = [0.0] * len(available)
    grouped_indices: dict[str, list[int]] = {}
    for index, (candidate, _, _) in enumerate(available):
        grouped_indices.setdefault(candidate.market, []).append(index)
    for indices in grouped_indices.values():
        group_ranks = _percentile_ranks([value_getter(available[index]) for index in indices])
        for index, rank in zip(indices, group_ranks):
            ranks[index] = rank
    return ranks


def _notional_volume(quote: dict) -> float:
    explicit = quote.get("day_notional_volume")
    if explicit is not None:
        return max(float(explicit or 0), 0.0)
    return max(float(quote.get("price") or 0) * float(quote.get("volume") or 0), 0.0)


def rank_recommendations(
    quotes: Iterable[Optional[dict]],
    watchlist_symbols: Iterable[str],
    *,
    limit: int = 5,
    candidates: tuple[RecommendationCandidate, ...] = RECOMMENDATION_CANDIDATES,
) -> list[dict]:
    """Rank available candidates while keeping the report market-diverse.

    The function deliberately uses only information available in the current
    close snapshot. Strategy validity and entry suitability are evaluated only
    after the user adds a recommendation to the watchlist.
    """

    limit = max(1, min(int(limit), 5))
    watched = {symbol.strip().upper() for symbol in watchlist_symbols}
    candidate_by_symbol = {item.symbol.upper(): item for item in candidates}

    available: list[tuple[RecommendationCandidate, dict, float]] = []
    for quote in quotes:
        if not quote:
            continue
        symbol = str(quote.get("symbol") or "").strip().upper()
        candidate = candidate_by_symbol.get(symbol)
        if candidate is None or symbol in watched:
            continue
        price = float(quote.get("price") or 0)
        if price <= 0:
            continue
        available.append((candidate, quote, _notional_volume(quote)))

    if not available:
        return []

    # Trading sessions and quote currencies differ across stocks, crypto and
    # ETFs. Rank liquidity/activity inside each market instead of comparing
    # their raw notionals directly.
    liquidity_ranks = _market_percentile_ranks(available, lambda item: log1p(item[2]))
    activity_ranks = _market_percentile_ranks(
        available,
        lambda item: abs(float(item[1].get("change_percent") or 0)),
    )

    ranked: list[dict] = []
    for index, (candidate, quote, notional) in enumerate(available):
        change_percent = float(quote.get("change_percent") or 0)
        score = (
            candidate.leadership_weight * 0.52
            + liquidity_ranks[index] * 0.33
            + activity_ranks[index] * 0.15
        )
        reasons = [candidate.tags[0]]
        if liquidity_ranks[index] >= 0.75:
            reasons.append("候选池成交额靠前")
        if abs(change_percent) >= 2:
            direction = "上涨" if change_percent > 0 else "回撤"
            reasons.append(f"收盘{direction} {abs(change_percent):.2f}%")
        elif len(candidate.tags) > 1:
            reasons.append(candidate.tags[1])

        ranked.append({
            "symbol": candidate.symbol,
            "name": candidate.name,
            "asset_type": candidate.asset_type,
            "market": candidate.market,
            "market_label": candidate.market_label,
            "tags": list(candidate.tags),
            "reasons": reasons[:3],
            "score": round(score, 4),
            "price": float(quote.get("price") or 0),
            "change_percent": change_percent,
            "notional_volume": notional,
            "source": quote.get("source"),
            "source_label": quote.get("source_label"),
        })

    ranked.sort(key=lambda item: (-item["score"], item["symbol"]))

    # Guarantee a broad daily view before filling remaining slots by score.
    selected: list[dict] = []
    selected_symbols: set[str] = set()
    for market in ("us_equity", "crypto", "cross_asset"):
        match = next((item for item in ranked if item["market"] == market), None)
        if match is not None and len(selected) < limit:
            selected.append(match)
            selected_symbols.add(match["symbol"])
    market_caps = {"us_equity": 2, "crypto": 2, "cross_asset": 1}
    market_counts = {
        market: sum(item["market"] == market for item in selected)
        for market in market_caps
    }
    for item in ranked:
        if len(selected) >= limit:
            break
        if (
            item["symbol"] not in selected_symbols
            and market_counts.get(item["market"], 0) < market_caps.get(item["market"], limit)
        ):
            selected.append(item)
            selected_symbols.add(item["symbol"])
            market_counts[item["market"]] = market_counts.get(item["market"], 0) + 1

    # If exclusions or upstream outages make a market sparse, fill the report
    # from whatever reliable candidates remain rather than returning fewer rows.
    for item in ranked:
        if len(selected) >= limit:
            break
        if item["symbol"] not in selected_symbols:
            selected.append(item)
            selected_symbols.add(item["symbol"])
    return selected
