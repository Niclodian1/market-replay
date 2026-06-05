import asyncio
from typing import Optional
from fastapi import APIRouter, Query, HTTPException
from app.providers.yahoo_finance import get_provider
from app.providers.local_crypto import pick_series_provider
from app.schemas.market import SearchResponse, QuoteResponse, CandlesResponse, BatchQuoteResponse

router = APIRouter()

VALID_INTERVALS = {"15min", "1h", "4h", "1day", "1week"}


@router.get("/search", response_model=SearchResponse)
async def search_symbols(q: str = Query(..., min_length=1, description="搜索关键词")):
    try:
        provider = get_provider()
        results = await provider.search(q)
        return {"results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/quote", response_model=QuoteResponse)
async def get_quote(symbol: str = Query(..., description="标的代码，如 AAPL 或 BTC/USD")):
    try:
        provider = get_provider()
        return await provider.get_quote(symbol)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/quotes")
async def get_quotes(symbols: str = Query(..., description="逗号分隔的标的代码，如 AAPL,MSFT,BTC-USD")):
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="至少需要一个标的代码")
    if len(symbol_list) > 30:
        raise HTTPException(status_code=400, detail="单次最多查询 30 个标的")

    provider = get_provider()
    results = await provider.get_quotes_batch(symbol_list)
    return {"quotes": results}


@router.get("/earnings")
async def get_earnings(symbols: str = Query(..., description="逗号分隔的标的代码")):
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="至少需要一个标的代码")
    if len(symbol_list) > 20:
        raise HTTPException(status_code=400, detail="单次最多查询 20 个标的")
    provider = get_provider()
    result = await provider.get_earnings_dates(symbol_list)
    return {"earnings": result}


@router.get("/candles", response_model=CandlesResponse)
async def get_candles(
    symbol: str = Query(..., description="标的代码"),
    timeframe: str = Query(default="1day", description="周期：15min/1h/4h/1day/1week"),
    outputsize: int = Query(default=500, ge=10, le=5000, description="K线数量"),
    before: Optional[int] = Query(default=None, description="Unix 时间戳，仅返回此时间之前的数据（用于动态加载历史）"),
):
    if timeframe not in VALID_INTERVALS:
        raise HTTPException(status_code=400, detail=f"timeframe 必须是 {VALID_INTERVALS} 之一")
    try:
        # crypto 且本地有 parquet → 走混合本地源；其余走 Yahoo
        provider = pick_series_provider(symbol, timeframe)
        candles = await provider.get_time_series(symbol, timeframe, outputsize, before=before)
        return {"symbol": symbol, "timeframe": timeframe, "candles": candles}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
