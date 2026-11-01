from fastapi import APIRouter, Query, HTTPException
from app.providers.local_crypto import pick_series_provider
from app.core.market_structure import analyze_structure, detect_swing_points, detect_sr_levels
from app.schemas.analysis import StructureResponse, SRResponse

router = APIRouter()

VALID_INTERVALS = {"15min", "1h", "4h", "1day", "1week"}


@router.get("/structure", response_model=StructureResponse)
async def get_structure(
    symbol: str = Query(..., description="标的代码"),
    timeframe: str = Query(default="1day", description="周期"),
    outputsize: int = Query(default=500, ge=50, le=5000),
    lookback: int = Query(default=5, ge=2, le=20, description="Swing 检测回看周期"),
):
    if timeframe not in VALID_INTERVALS:
        raise HTTPException(status_code=400, detail=f"无效 timeframe: {timeframe}")
    try:
        provider = pick_series_provider(symbol, timeframe)
        candles = await provider.get_time_series(symbol, timeframe, outputsize)
        result = analyze_structure(candles, lookback=lookback)
        return {"symbol": symbol, "timeframe": timeframe, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/support-resistance", response_model=SRResponse)
async def get_support_resistance(
    symbol: str = Query(..., description="标的代码"),
    timeframe: str = Query(default="1day", description="周期"),
    outputsize: int = Query(default=500, ge=50, le=5000),
    lookback: int = Query(default=5, ge=2, le=20),
    tolerance: float = Query(default=0.5, ge=0.1, le=3.0, description="聚类容差百分比"),
    max_levels: int = Query(default=10, ge=3, le=30),
):
    if timeframe not in VALID_INTERVALS:
        raise HTTPException(status_code=400, detail=f"无效 timeframe: {timeframe}")
    try:
        provider = pick_series_provider(symbol, timeframe)
        candles = await provider.get_time_series(symbol, timeframe, outputsize)
        swings = detect_swing_points(candles, lookback)
        levels = detect_sr_levels(candles, swings, tolerance, max_levels)
        return {"symbol": symbol, "timeframe": timeframe, "levels": levels}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
