from pydantic import BaseModel
from typing import Optional


class EconomicEvent(BaseModel):
    date: str
    time_bjt: Optional[str] = None
    name: str
    importance: str


class CalendarResponse(BaseModel):
    events: list[EconomicEvent]


class SectorItem(BaseModel):
    name: str
    symbol: str
    change_pct: float
    price: float


class CryptoSectorItem(BaseModel):
    name: str
    symbols: list[str]
    avg_change_pct: float


class SectorsResponse(BaseModel):
    stock_sectors: list[SectorItem]
    crypto_sectors: list[CryptoSectorItem]


class CorrelationResponse(BaseModel):
    symbols: list[str]
    matrix: list[list[Optional[float]]]
