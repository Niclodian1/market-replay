from typing import Optional

from fastapi import APIRouter, Query

from app.core.fundamentals_service import fundamentals_service
from app.schemas.fundamentals import (
    EarningsCalendar,
    FundamentalsDetailResponse,
    FundamentalsListResponse,
    ReloadResponse,
)

router = APIRouter()


@router.get("/list", response_model=FundamentalsListResponse)
async def list_fundamentals(
    market: Optional[str] = Query(default=None, pattern="^(US|HK|CN)$"),
    q: Optional[str] = Query(default=None),
):
    return await fundamentals_service.list_companies(market=market, q=q)


@router.post("/reload", response_model=ReloadResponse)
async def reload_fundamentals():
    total = fundamentals_service.reload_profiles()
    return ReloadResponse(ok=True, total=total)


@router.get("/{symbol}", response_model=FundamentalsDetailResponse)
async def get_fundamentals(symbol: str):
    return await fundamentals_service.get_detail(symbol)


@router.get("/{symbol}/earnings", response_model=EarningsCalendar)
async def get_fundamentals_earnings(symbol: str):
    return await fundamentals_service.get_earnings(symbol)
