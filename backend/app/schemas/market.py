from pydantic import BaseModel
from typing import Optional


class SearchResult(BaseModel):
    symbol: str
    name: str
    type: str
    exchange: str
    currency: str


class SearchResponse(BaseModel):
    results: list[SearchResult]


class QuoteResponse(BaseModel):
    symbol: str
    name: str
    price: float
    change: float
    change_percent: float
    volume: int
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
