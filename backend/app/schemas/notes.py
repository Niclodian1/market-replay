from datetime import datetime
from typing import Optional
from pydantic import BaseModel


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
