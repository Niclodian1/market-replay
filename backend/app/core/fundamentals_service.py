from typing import Optional

from app.core.profile_loader import profile_loader
from app.providers.yahoo_finance import get_provider
from app.schemas.fundamentals import (
    EarningsCalendar,
    FinancialSnapshot,
    FundamentalsDetailResponse,
    FundamentalsListResponse,
)


class FundamentalsService:
    async def list_companies(self, market: Optional[str] = None, q: Optional[str] = None) -> FundamentalsListResponse:
        items = profile_loader.list_profiles(market=market, q=q)
        return FundamentalsListResponse(items=items, total=len(items))

    async def get_detail(self, symbol: str) -> FundamentalsDetailResponse:
        provider = get_provider()
        profile = profile_loader.get_profile(symbol)
        source_status = {"profile": "hit" if profile else "missing", "snapshot": "ok", "earnings": "ok"}

        snapshot: Optional[FinancialSnapshot] = None
        earnings: Optional[EarningsCalendar] = None
        try:
            snapshot = FinancialSnapshot(**await provider.get_financial_snapshot(symbol))
        except Exception as exc:
            source_status["snapshot"] = f"unavailable: {exc}"

        try:
            earnings = EarningsCalendar(**await provider.get_earnings_history(symbol, n=4))
        except Exception as exc:
            source_status["earnings"] = f"unavailable: {exc}"

        return FundamentalsDetailResponse(
            symbol=symbol,
            profile=profile,
            snapshot=snapshot,
            earnings=earnings,
            source_status=source_status,
        )

    async def get_earnings(self, symbol: str) -> EarningsCalendar:
        provider = get_provider()
        return EarningsCalendar(**await provider.get_earnings_history(symbol, n=4))

    def reload_profiles(self) -> int:
        return profile_loader.reload()


fundamentals_service = FundamentalsService()
