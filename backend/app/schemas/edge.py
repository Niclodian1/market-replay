from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel


class EdgeStatOut(BaseModel):
    id: int
    symbol: str
    timeframe: str
    combo_hash: str
    combo_spec: dict[str, Any]
    direction: str
    sample_size: int
    wins: int
    win_rate: float
    avg_r: float
    expectancy: float
    sharpe_like: float
    wilson_lower: float
    window_start_ts: int
    window_end_ts: int
    last_updated: datetime

    model_config = {"from_attributes": True}


class EdgeStatsResponse(BaseModel):
    items: list[EdgeStatOut]
    total: int


class Thresholds(BaseModel):
    min_winrate: float = 0.55
    min_sample: int = 30
    min_score: float = 0.6
    max_open_per_symbol: int = 3


class ExplainRequest(BaseModel):
    symbol: str
    timeframe: str = "4h"
    direction: str = "long"
    signals: list[str]
    entry: float
    atr: Optional[float] = None


class ExplainResponse(BaseModel):
    combo_hash: str
    score: float
    trade_plan: dict[str, Any]
