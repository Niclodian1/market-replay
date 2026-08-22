from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: str = "discord"
    config: dict[str, Any]
    is_default: bool = False


class ChannelUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    config: Optional[dict[str, Any]] = None
    is_default: Optional[bool] = None
    is_active: Optional[bool] = None


class ChannelOut(BaseModel):
    id: int
    name: str
    kind: str
    # database or environment fallback; environment rows are read-only
    source: str = "database"
    masked_endpoint: str
    settings: dict[str, Any] = Field(default_factory=dict)
    is_default: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ChannelsResponse(BaseModel):
    items: list[ChannelOut]


class MonitorPlanCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    target_scope: str = "watchlist"
    timeframes: Optional[list[str]] = None
    # Deprecated single-timeframe input, retained for older API clients.
    timeframe: Optional[str] = None
    strategies: list[str] = Field(default_factory=lambda: ["ma"])
    parameters: dict[str, Any] = Field(default_factory=dict)
    channel_id: Optional[int] = None
    repeating: bool = True


class MonitorPlanUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    timeframes: Optional[list[str]] = None
    # Deprecated single-timeframe input, retained for older API clients.
    timeframe: Optional[str] = None
    strategies: Optional[list[str]] = None
    parameters: Optional[dict[str, Any]] = None
    channel_id: Optional[int] = None
    use_default_channel: Optional[bool] = None
    is_active: Optional[bool] = None


class MonitorPlanOut(BaseModel):
    id: int
    name: str
    target_scope: str
    timeframes: list[str]
    # First selected timeframe, retained for backwards compatibility.
    timeframe: str
    strategies: list[str]
    parameters: dict[str, Any]
    channel_id: Optional[int]
    channel_name: Optional[str]
    repeating: bool
    is_active: bool
    last_run_at: Optional[datetime]
    last_error: Optional[str]
    created_at: datetime
    updated_at: datetime


class MonitorPlansResponse(BaseModel):
    items: list[MonitorPlanOut]


class AlertEventOut(BaseModel):
    id: int
    plan_id: int
    symbol: str
    timeframe: str
    strategy: str
    event_type: str
    direction: str
    severity: str
    signal_time: int
    reference_price: float
    payload: dict[str, Any]
    status: str
    attempts: int
    sent_at: Optional[datetime]
    last_error: Optional[str]
    is_read: bool
    created_at: datetime


class AlertEventsResponse(BaseModel):
    items: list[AlertEventOut]
    total: int
    unread: int
