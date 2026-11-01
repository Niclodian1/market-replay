from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field


# ───── Watchlist ─────────────────────────────────────────────────────────────

class WatchlistCreate(BaseModel):
    symbol: str
    name: str
    asset_type: str = "stock"


class WatchlistItem(BaseModel):
    id: int
    symbol: str
    name: str
    asset_type: str
    created_at: datetime

    model_config = {"from_attributes": True}


class WatchlistResponse(BaseModel):
    items: list[WatchlistItem]


class WatchlistOpportunity(BaseModel):
    strategy: Literal["A", "B", "C"]
    parameters: list[str]
    timeframe: Literal["1day"] = "1day"
    signal_time: int


class WatchlistInsight(BaseModel):
    symbol: str
    current_state: Optional[Literal["bullish", "bearish", "sideways"]] = None
    opportunities: list[WatchlistOpportunity] = Field(default_factory=list)
    error: Optional[str] = None


class WatchlistInsightsResponse(BaseModel):
    generated_at: datetime
    timeframe: Literal["1day"] = "1day"
    items: list[WatchlistInsight]


class WatchlistRecommendation(BaseModel):
    symbol: str
    name: str
    asset_type: str
    market: str
    market_label: str
    tags: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    score: float
    price: float
    change_percent: float
    notional_volume: float
    source: Optional[str] = None
    source_label: Optional[str] = None


class WatchlistRecommendationsResponse(BaseModel):
    generated_at: datetime
    items: list[WatchlistRecommendation]
    scanned_count: int
    available_count: int
    max_items: int = 5
    note: str = "建议加入自选，不代表建议立即交易"


# ───── ReplayNote ─────────────────────────────────────────────────────────────

class ReplayNoteCreate(BaseModel):
    symbol: str
    timeframe: str
    replay_start_time: str   # ISO 字符串，后端解析为 datetime
    note_time: str           # ISO 字符串
    trend_view: Optional[str] = None
    support_level: Optional[float] = None
    resistance_level: Optional[float] = None
    entry_idea: Optional[str] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    summary: Optional[str] = None


class ReplayNoteOut(BaseModel):
    id: int
    symbol: str
    timeframe: str
    replay_start_time: datetime
    note_time: datetime
    trend_view: Optional[str]
    support_level: Optional[float]
    resistance_level: Optional[float]
    entry_idea: Optional[str]
    stop_loss: Optional[float]
    take_profit: Optional[float]
    summary: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReplayNotesResponse(BaseModel):
    items: list[ReplayNoteOut]
    total: int
