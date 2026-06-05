from typing import Any, Optional

from pydantic import BaseModel, Field


class RevenueSegment(BaseModel):
    segment: str
    pct: float


class CompanyProfile(BaseModel):
    symbol: str
    market: str
    name_cn: Optional[str] = None
    name_en: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    business_overview: Optional[str] = None
    equity_story: Optional[str] = None
    revenue_model: Optional[str] = None
    revenue_breakdown: list[RevenueSegment] = Field(default_factory=list)
    moat: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)
    updated_by: Optional[str] = None
    updated_at: Optional[str] = None
    reviewed: bool = False


class CompanyProfileCard(BaseModel):
    symbol: str
    market: str
    name_cn: Optional[str] = None
    name_en: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    business_overview: Optional[str] = None
    reviewed: bool = False


class FundamentalsListResponse(BaseModel):
    items: list[CompanyProfileCard]
    total: int


class FinancialSnapshot(BaseModel):
    symbol: str
    revenue: Optional[float] = None
    net_income: Optional[float] = None
    revenue_yoy: Optional[float] = None
    revenue_qoq: Optional[float] = None
    net_income_yoy: Optional[float] = None
    net_income_qoq: Optional[float] = None
    gross_margin: Optional[float] = None
    market_cap: Optional[float] = None
    trailing_pe: Optional[float] = None
    forward_pe: Optional[float] = None
    roe: Optional[float] = None
    dividend_yield: Optional[float] = None
    currency: Optional[str] = None
    quarter: Optional[str] = None
    updated_at: Optional[str] = None


class EarningsHistoryItem(BaseModel):
    date: str
    eps_actual: Optional[float] = None
    eps_estimate: Optional[float] = None
    surprise_pct: Optional[float] = None


class EarningsCalendar(BaseModel):
    symbol: str
    next_earnings_date: Optional[str] = None
    history: list[EarningsHistoryItem] = Field(default_factory=list)


class FundamentalsDetailResponse(BaseModel):
    symbol: str
    profile: Optional[CompanyProfile] = None
    snapshot: Optional[FinancialSnapshot] = None
    earnings: Optional[EarningsCalendar] = None
    source_status: dict[str, Any] = Field(default_factory=dict)


class ReloadResponse(BaseModel):
    ok: bool
    total: int
