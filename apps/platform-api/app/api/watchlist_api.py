import asyncio
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.candle_utils import completed_daily_candles
from app.core.signals.monitor_signals import detect_ma_events
from app.core.market_recommendations import RECOMMENDATION_CANDIDATES, rank_recommendations
from app.api.market import get_quotes as get_market_quotes
from app.db.session import get_db
from app.models.models import Watchlist, User
from app.providers.local_crypto import pick_series_provider
from app.schemas.notes import (
    WatchlistCreate,
    WatchlistInsight,
    WatchlistInsightsResponse,
    WatchlistItem,
    WatchlistRecommendationsResponse,
    WatchlistResponse,
)

router = APIRouter()
_INSIGHTS_TTL_SECONDS = 300
_insights_cache: dict[tuple[str, ...], tuple[float, WatchlistInsightsResponse]] = {}
_RECOMMENDATIONS_TTL_SECONDS = 300
_recommendation_quotes_cache: tuple[float, list[dict | None]] | None = None

# 单用户应用：固定使用 user_id=1，启动时自动创建


async def _ensure_default_user(db: AsyncSession) -> int:
    result = await db.execute(select(User).where(User.id == 1))
    user = result.scalar_one_or_none()
    if not user:
        user = User(id=1, username="default", password_hash="unused")
        db.add(user)
        await db.commit()
    return 1


async def _watchlist_items(db: AsyncSession, user_id: int) -> list[Watchlist]:
    result = await db.execute(
        select(Watchlist)
        .where(Watchlist.user_id == user_id)
        .order_by(Watchlist.created_at.desc())
    )
    return list(result.scalars().all())


@router.get("", response_model=WatchlistResponse)
async def get_watchlist(db: AsyncSession = Depends(get_db)):
    user_id = await _ensure_default_user(db)
    items = await _watchlist_items(db, user_id)
    return WatchlistResponse(items=items)


async def _compute_insight(symbol: str, semaphore: asyncio.Semaphore) -> WatchlistInsight:
    async with semaphore:
        try:
            provider = pick_series_provider(symbol, "1day")
            # Keep the longest series exposed by the provider so Wilder RSI(14)
            # matches the existing backtest calculation as closely as possible.
            candles = await provider.get_time_series(symbol, "1day", 5000)
            completed = completed_daily_candles(symbol, candles)
            # 复用监控计划 MA 突破检测器（口径统一：MA 30/60/99/120 ATR 归一化突破）
            events = detect_ma_events(completed, "1day")
            opportunities = [
                {
                    "strategy": "A" if event["direction"] == "long" else "C",
                    "parameters": [
                        str(event["payload"]["parameters"]["period"])
                    ],
                    "timeframe": "1day",
                    "signal_time": int(completed[-1]["time"]),
                }
                for event in events
            ]
            return WatchlistInsight(symbol=symbol, opportunities=opportunities)
        except Exception as exc:
            return WatchlistInsight(
                symbol=symbol,
                opportunities=[],
                error=str(exc)[:200] or "行情数据暂不可用",
            )


@router.get("/insights", response_model=WatchlistInsightsResponse)
async def get_watchlist_insights(db: AsyncSession = Depends(get_db)):
    user_id = await _ensure_default_user(db)
    items = await _watchlist_items(db, user_id)
    symbols = tuple(item.symbol for item in items)

    cached = _insights_cache.get(symbols)
    if cached is not None and time.monotonic() - cached[0] < _INSIGHTS_TTL_SECONDS:
        return cached[1]

    semaphore = asyncio.Semaphore(5)
    insights = await asyncio.gather(*(_compute_insight(symbol, semaphore) for symbol in symbols))
    response = WatchlistInsightsResponse(
        generated_at=datetime.now(timezone.utc),
        items=list(insights),
    )
    _insights_cache[symbols] = (time.monotonic(), response)

    # Keep the process-local cache bounded as users add/remove symbols.
    if len(_insights_cache) > 32:
        oldest_key = min(_insights_cache, key=lambda key: _insights_cache[key][0])
        if oldest_key != symbols:
            _insights_cache.pop(oldest_key, None)
    return response


async def _recommendation_quotes() -> list[dict | None]:
    global _recommendation_quotes_cache
    now = time.monotonic()
    if (
        _recommendation_quotes_cache is not None
        and now - _recommendation_quotes_cache[0] < _RECOMMENDATIONS_TTL_SECONDS
    ):
        return _recommendation_quotes_cache[1]

    symbols = ",".join(candidate.symbol for candidate in RECOMMENDATION_CANDIDATES)
    try:
        payload = await get_market_quotes(symbols)
        quotes = list(payload.get("quotes") or [])
        _recommendation_quotes_cache = (now, quotes)
        return quotes
    except Exception:
        # A stale daily snapshot is more useful than making the whole report
        # disappear during a short upstream outage.
        if _recommendation_quotes_cache is not None and now - _recommendation_quotes_cache[0] < 3600:
            return _recommendation_quotes_cache[1]
        return []


@router.get("/recommendations", response_model=WatchlistRecommendationsResponse)
async def get_watchlist_recommendations(
    limit: int = 5,
    db: AsyncSession = Depends(get_db),
):
    user_id = await _ensure_default_user(db)
    watchlist = await _watchlist_items(db, user_id)
    quotes = await _recommendation_quotes()
    items = rank_recommendations(
        quotes,
        (item.symbol for item in watchlist),
        limit=limit,
    )
    return WatchlistRecommendationsResponse(
        generated_at=datetime.now(timezone.utc),
        items=items,
        scanned_count=len(RECOMMENDATION_CANDIDATES),
        available_count=sum(quote is not None for quote in quotes),
        max_items=min(max(limit, 1), 5),
    )


@router.post("", response_model=WatchlistItem, status_code=201)
async def add_watchlist(body: WatchlistCreate, db: AsyncSession = Depends(get_db)):
    user_id = await _ensure_default_user(db)
    # 避免重复
    existing = await db.execute(
        select(Watchlist).where(Watchlist.user_id == user_id, Watchlist.symbol == body.symbol)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Symbol already in watchlist")
    item = Watchlist(user_id=user_id, symbol=body.symbol, name=body.name, asset_type=body.asset_type)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.delete("/{item_id}", status_code=204)
async def delete_watchlist(item_id: int, db: AsyncSession = Depends(get_db)):
    user_id = await _ensure_default_user(db)
    result = await db.execute(
        select(Watchlist).where(Watchlist.id == item_id, Watchlist.user_id == user_id)
    )
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    await db.delete(item)
    await db.commit()
