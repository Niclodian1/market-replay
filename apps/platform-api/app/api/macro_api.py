import asyncio
from fastapi import APIRouter, Query, HTTPException
from app.providers.yahoo_finance import get_provider
from app.core.economic_calendar import generate_calendar
from app.core.crypto_sectors import CRYPTO_SECTORS, STOCK_SECTOR_ETFS
from app.core.correlation import compute_correlation_matrix
from app.schemas.macro import CalendarResponse, SectorsResponse, CorrelationResponse

router = APIRouter()


@router.get("/calendar", response_model=CalendarResponse)
async def get_calendar(
    days_ahead: int = Query(default=60, ge=1, le=180),
    days_back: int = Query(default=7, ge=0, le=30),
):
    events = generate_calendar(days_back=days_back, days_ahead=days_ahead)
    return {"events": events}


@router.get("/sectors", response_model=SectorsResponse)
async def get_sectors(
    period: str = Query(default="1d", description="1d, 1w, 1m"),
):
    provider = get_provider()

    # Stock sectors
    etf_symbols = list(STOCK_SECTOR_ETFS.values())
    etf_names = list(STOCK_SECTOR_ETFS.keys())

    async def safe_quote(sym: str):
        try:
            return await provider.get_quote(sym)
        except Exception:
            return None

    stock_quotes = await asyncio.gather(*[safe_quote(s) for s in etf_symbols])
    stock_sectors = []
    for i, q in enumerate(stock_quotes):
        if q:
            stock_sectors.append({
                "name": etf_names[i],
                "symbol": etf_symbols[i],
                "change_pct": round(q.get("change_percent", 0), 2),
                "price": round(q.get("price", 0), 2),
            })
    stock_sectors.sort(key=lambda x: x["change_pct"], reverse=True)

    # Crypto sectors
    crypto_results = []
    for sector_name, symbols in CRYPTO_SECTORS.items():
        quotes = await asyncio.gather(*[safe_quote(s) for s in symbols])
        valid = [q for q in quotes if q and q.get("change_percent") is not None]
        avg_change = sum(q["change_percent"] for q in valid) / len(valid) if valid else 0
        crypto_results.append({
            "name": sector_name,
            "symbols": symbols,
            "avg_change_pct": round(avg_change, 2),
        })
    crypto_results.sort(key=lambda x: x["avg_change_pct"], reverse=True)

    return {"stock_sectors": stock_sectors, "crypto_sectors": crypto_results}


@router.get("/correlation", response_model=CorrelationResponse)
async def get_correlation(
    symbols: str = Query(..., description="逗号分隔的标的代码"),
    window: int = Query(default=60, ge=10, le=365),
    timeframe: str = Query(default="1day"),
):
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if len(symbol_list) < 2:
        raise HTTPException(status_code=400, detail="至少需要 2 个标的")
    if len(symbol_list) > 12:
        raise HTTPException(status_code=400, detail="最多支持 12 个标的")

    provider = get_provider()
    outputsize = window + 10

    async def get_closes(sym: str):
        try:
            candles = await provider.get_time_series(sym, timeframe, outputsize)
            return sym, candles
        except Exception:
            return sym, []

    results = await asyncio.gather(*[get_closes(s) for s in symbol_list])
    symbol_closes = {sym: candles for sym, candles in results if candles}

    if len(symbol_closes) < 2:
        raise HTTPException(status_code=400, detail="有效数据不足，至少需要 2 个标的有数据")

    matrix = compute_correlation_matrix(symbol_closes, window)
    return matrix
