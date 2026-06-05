from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel

from app.schemas.edge import EdgeStatOut


class TradePlanOut(BaseModel):
    id: int
    opportunity_id: int
    entry: float
    stop_loss: float
    take_profit: float
    rr_ratio: float
    size_r: float
    rationale: str
    valid_until_ts: int
    created_at: datetime

    model_config = {"from_attributes": True}


class OpportunityOut(BaseModel):
    id: int
    symbol: str
    timeframe: str
    direction: str
    combo_hash: str
    confluence_payload: dict[str, Any]
    score: float
    status: str
    created_at: datetime
    closed_at: Optional[datetime]
    realized_r: Optional[float]
    edge_stat: Optional[EdgeStatOut] = None
    trade_plan: Optional[TradePlanOut] = None

    model_config = {"from_attributes": True}


class OpportunitiesResponse(BaseModel):
    items: list[OpportunityOut]
    total: int
