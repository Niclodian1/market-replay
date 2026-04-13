from fastapi import APIRouter, Query, HTTPException
from app.providers.yahoo_finance import get_provider
from app.core.indicators import calculate_indicators
from app.schemas.market import IndicatorsResponse

router = APIRouter()

VALID_INTERVALS = {"15min", "1h", "4h", "1day", "1week"}


@router.get("", response_model=IndicatorsResponse)
async def get_indicators(
    symbol: str = Query(..., description="标的代码"),
    timeframe: str = Query(default="1day", description="周期"),
    outputsize: int = Query(default=300, ge=10, le=5000),
):
    if timeframe not in VALID_INTERVALS:
        raise HTTPException(status_code=400, detail=f"无效 timeframe: {timeframe}")
    try:
        provider = get_provider()
        # 复用缓存：与 /candles 相同参数时不重复请求 API
        candles = await provider.get_time_series(symbol, timeframe, outputsize)
        indicators = calculate_indicators(candles)
        return {"symbol": symbol, "timeframe": timeframe, **indicators}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
