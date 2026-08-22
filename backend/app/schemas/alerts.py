from datetime import datetime
from typing import Optional
from pydantic import BaseModel, field_serializer

VALID_CONDITIONS = {
    "price_above", "price_below",
    "ma_cross", "rsi", "macd", "ma_breakout", "strategy_a_breakout",
    "macd_divergence", "bollinger",
    "sr_breakout", "pinbar", "engulfing",
    "w_bottom",
}

CONDITION_LABELS = {
    "price_above": "价格突破（上穿）",
    "price_below": "价格跌破（下穿）",
    "ma_cross": "均线交叉（金叉/死叉）",
    "rsi": "RSI 超买超卖",
    "macd": "MACD 交叉",
    "ma_breakout": "价格突破均线",
    "strategy_a_breakout": "策略A · 日线均线突破",
    "macd_divergence": "MACD 柱极值背离",
    "bollinger": "布林带触及",
    "sr_breakout": "支撑阻力突破",
    "pinbar": "Pinbar 锤子线",
    "engulfing": "吞没形态",
    "w_bottom": "W底双底",
}


class AlertCreate(BaseModel):
    name: str
    symbol: str
    condition_type: str
    threshold: Optional[float] = None
    timeframe: str = "1day"
    discord_webhook_url: str
    ma_period: Optional[int] = None
    repeating: bool = False


class AlertUpdate(BaseModel):
    name: Optional[str] = None
    threshold: Optional[float] = None
    timeframe: Optional[str] = None
    discord_webhook_url: Optional[str] = None
    is_active: Optional[bool] = None
    ma_period: Optional[int] = None
    repeating: Optional[bool] = None


class AlertOut(BaseModel):
    id: int
    name: str
    symbol: str
    condition_type: str
    threshold: Optional[float]
    timeframe: str
    discord_webhook_url: str
    ma_period: Optional[int]
    repeating: bool
    is_active: bool
    is_triggered: bool
    triggered_at: Optional[datetime]
    last_triggered_signal_time: Optional[int]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("discord_webhook_url")
    def mask_webhook(self, value: str) -> str:
        if len(value) <= 18:
            return "••••••"
        return f"{value[:12]}…{value[-6:]}"


class AlertsResponse(BaseModel):
    items: list[AlertOut]
    total: int
