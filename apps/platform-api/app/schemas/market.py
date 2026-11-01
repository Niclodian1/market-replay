from pydantic import BaseModel
from typing import Optional


class SearchResult(BaseModel):
    symbol: str
    display_symbol: Optional[str] = None
    name: str
    type: str
    exchange: str
    currency: str
    source: str = "yahoo"
    source_label: str = "Yahoo Finance"


class SearchResponse(BaseModel):
    results: list[SearchResult]


class QuoteResponse(BaseModel):
    symbol: str
    provider_symbol: Optional[str] = None
    display_symbol: Optional[str] = None
    name: str
    price: float
    change: float
    change_percent: float
    volume: float
    day_notional_volume: Optional[float] = None
    funding_rate: Optional[float] = None
    open_interest: Optional[float] = None
    open_interest_value: Optional[float] = None
    mark_price: Optional[float] = None
    oracle_price: Optional[float] = None
    max_leverage: Optional[int] = None
    source: str = "yahoo"
    source_label: str = "Yahoo Finance"
    timestamp: str


class Candle(BaseModel):
    time: int       # Unix timestamp (UTC 秒)
    open: float
    high: float
    low: float
    close: float
    volume: float


class CandlesResponse(BaseModel):
    symbol: str
    provider_symbol: Optional[str] = None
    source: str = "yahoo"
    timeframe: str
    candles: list[Candle]


class MAPoint(BaseModel):
    time: int
    value: float


class RSIPoint(BaseModel):
    time: int
    value: float


class MACDPoint(BaseModel):
    time: int
    macd: float
    signal: float
    histogram: float


class IndicatorsResponse(BaseModel):
    symbol: str
    timeframe: str
    ma20: list[MAPoint]
    ma60: list[MAPoint]
    rsi: list[RSIPoint]
    macd: list[MACDPoint]


class BatchQuoteResponse(BaseModel):
    quotes: list[Optional[QuoteResponse]]


class ErrorResponse(BaseModel):
    detail: str
