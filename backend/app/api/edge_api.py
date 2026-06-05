from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.edge_service import get_thresholds_db, update_thresholds_db
from app.core.candle_store import sync_candles
from app.db.session import get_db
from app.models.models import EdgeStat, Watchlist
from app.schemas.edge import EdgeStatsResponse, Thresholds
from app.tasks.edge_discovery import run_edge_discovery_for_pair, run_edge_discovery_once
from app.tasks.outcome_tracking import run_outcome_backfill

router = APIRouter()


@router.get("/{symbol}", response_model=EdgeStatsResponse)
async def list_edge_stats(
    symbol: str,
    tf: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(EdgeStat).where(EdgeStat.symbol == symbol.upper())
    if tf:
        stmt = stmt.where(EdgeStat.timeframe == tf)
    stmt = stmt.order_by(EdgeStat.expectancy.desc()).limit(min(max(limit, 1), 200))
    result = await db.execute(stmt)
    items = list(result.scalars().all())
    return EdgeStatsResponse(items=items, total=len(items))


@router.get("/{symbol}/top", response_model=EdgeStatsResponse)
async def top_edge_stats(
    symbol: str,
    tf: str | None = None,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
):
    return await list_edge_stats(symbol=symbol, tf=tf, limit=limit, db=db)


@router.get("/settings/thresholds", response_model=Thresholds)
async def read_thresholds(db: AsyncSession = Depends(get_db)):
    return await get_thresholds_db(db)


@router.put("/settings/thresholds", response_model=Thresholds)
async def write_thresholds(body: Thresholds, db: AsyncSession = Depends(get_db)):
    return await update_thresholds_db(db, body.model_dump())


@router.post("/discover/run", status_code=status.HTTP_202_ACCEPTED)
async def run_edge_discovery(db: AsyncSession = Depends(get_db)):
    result = await run_edge_discovery_once(db)
    return {"accepted": True, **result}


@router.post("/discover/scan", status_code=status.HTTP_202_ACCEPTED)
async def scan_edge_for_symbol(
    symbol: str = Query(..., min_length=1),
    timeframe: str = Query("4h"),
    sync: bool = Query(True),
    db: AsyncSession = Depends(get_db),
):
    normalized_symbol = symbol.upper()
    inserted = 0
    if sync:
        inserted = await sync_candles(normalized_symbol, timeframe, db)
    result = await run_edge_discovery_for_pair(db, normalized_symbol, timeframe)
    return {"accepted": True, "inserted_candles": inserted, **result}


@router.post("/discover/watchlist", status_code=status.HTTP_202_ACCEPTED)
async def scan_edge_for_watchlist(
    timeframe: str = Query("4h"),
    sync: bool = Query(True),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Watchlist.symbol)
        .where(Watchlist.user_id == 1)
        .order_by(Watchlist.created_at.desc())
    )
    symbols = [row[0].upper() for row in result.all()]
    totals = {
        "symbols": 0,
        "candles": 0,
        "inserted_candles": 0,
        "combos": 0,
        "upserts": 0,
        "opportunities": 0,
        "signal_events": 0,
    }
    for symbol in symbols:
        inserted = 0
        if sync:
            inserted = await sync_candles(symbol, timeframe, db)
        scan = await run_edge_discovery_for_pair(db, symbol, timeframe)
        totals["symbols"] += scan.get("symbols", 0)
        totals["candles"] += scan.get("candles", 0)
        totals["inserted_candles"] += inserted
        totals["combos"] += scan.get("combos", 0)
        totals["upserts"] += scan.get("upserts", 0)
        totals["opportunities"] += scan.get("opportunities", 0)
        totals["signal_events"] += scan.get("signal_events", 0)
    return {"accepted": True, "watchlist_count": len(symbols), **totals}


@router.post("/outcomes/backfill", status_code=status.HTTP_202_ACCEPTED)
async def backfill_signal_outcomes(
    symbol: str | None = Query(None),
    timeframe: str | None = Query(None),
    horizon: int = Query(12, ge=1, le=200),
    limit: int = Query(500, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
):
    result = await run_outcome_backfill(
        db,
        symbol=symbol,
        timeframe=timeframe,
        horizon=horizon,
        limit=limit,
    )
    return {"accepted": True, **result}
