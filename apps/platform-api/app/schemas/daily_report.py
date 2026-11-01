from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


MarketName = Literal["crypto", "a_share", "us_stock", "commodity"]


class InstrumentAnalysis(BaseModel):
    symbol: str
    name: str
    tags: list[str] = Field(default_factory=list)
    session_date: str
    close: float
    change_percent: float
    low: float
    high: float
    amplitude_percent: float
    volume_change_percent: float | None = None
    volume_applicable: bool = True
    ma20: float | None = None
    ma60: float | None = None
    ma120: float | None = None
    ma_distance_percent: dict[str, float | None] = Field(default_factory=dict)
    ma_positions: dict[str, Literal["above", "below", "nearby", "unknown"]] = Field(default_factory=dict)
    ma_slopes: dict[str, Literal["up", "down", "flat", "unknown"]] = Field(default_factory=dict)
    ma_structure: str = ""
    stage: Literal["上涨趋势", "下跌趋势", "趋势不明确", "数据不足"]
    tactical_phase: Literal["continuation", "pullback", "rebound", "unclear"] = "unclear"
    momentum_state: str = "unknown"
    momentum_summary: str = ""
    trade_advice: str = ""
    risk_focus: str = ""
    conclusion: str


class OpportunityItem(BaseModel):
    market: MarketName
    symbol: str
    name: str
    direction: Literal["long", "short"]
    event_type: Literal[
        "ma_breakout", "macd_extreme_divergence",
        "chanlun_buy1", "chanlun_buy2", "chanlun_buy3",
        "chanlun_sell1", "chanlun_sell2", "chanlun_sell3",
        "chanlun_beichi_buy", "chanlun_beichi_sell",
    ]
    signal_name: str
    stars: int = Field(ge=1, le=5)
    strength_label: str
    summary: str
    signal_time: int


class ChanlunSignalSummary(BaseModel):
    k: str
    direction: Literal["long", "short"]
    state: str | None = None
    tier: str | None = None
    t: int
    price: float
    why: str = ""
    sub: str = ""
    upper: str = ""


class ChanlunSymbolSummary(BaseModel):
    symbol: str
    name: str
    timeframe: str = "1day"
    signals: list[ChanlunSignalSummary] = Field(default_factory=list)
    edge: str = ""


class ChanlunChapter(BaseModel):
    entries: list[ChanlunSymbolSummary] = Field(default_factory=list)


class CalendarEventItem(BaseModel):
    category: Literal["FOMC", "NFP", "CPI", "EARNINGS"]
    name: str
    starts_at: str
    timing: str | None = None


class MarketSection(BaseModel):
    market: MarketName
    title: str
    session_date: str | None = None
    status: Literal["ok", "unavailable"] = "ok"
    error: str | None = None
    instruments: list[InstrumentAnalysis] = Field(default_factory=list)
    hot_symbols: list[str] = Field(default_factory=list)


class DailyReportPayload(BaseModel):
    report_date: str
    timezone: str
    generated_at: str
    markets: list[MarketSection] = Field(default_factory=list)
    opportunities: list[OpportunityItem] = Field(default_factory=list)
    events: list[CalendarEventItem] = Field(default_factory=list)
    chanlun: ChanlunChapter | None = None


class DailyReportPreview(BaseModel):
    subject: str
    text: str
    html: str
    discord_summary: str
    payload: DailyReportPayload
