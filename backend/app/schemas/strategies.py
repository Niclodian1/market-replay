from datetime import datetime
from typing import Literal
from pydantic import BaseModel


class StrategyCard(BaseModel):
    id: str
    name: str
    category: Literal["indicator", "pattern", "structure", "combo"]
    description: str
    logic: str
    direction_support: list[str]
    params_schema: dict
    coverage: int
    avg_win_rate: float | None
    top_symbol: str | None
    latest_backtest: "StrategyBacktestSummary | None" = None


class StrategySymbolPerf(BaseModel):
    symbol: str
    timeframe: str
    direction: str
    sample_size: int
    win_rate: float
    avg_r: float
    expectancy: float
    max_drawdown: float
    last_fired_at: datetime | None
    confidence: Literal["low", "mid", "high"]
    source: Literal["edge", "backtest"] = "edge"
    latest_backtest_run_id: int | None = None


class StrategyDetail(BaseModel):
    card: StrategyCard
    rows: list[StrategySymbolPerf]
    sort_by: str
    sort_dir: Literal["asc", "desc"]
    generated_at: datetime


class StrategyTrade(BaseModel):
    symbol: str
    timeframe: str
    direction: str
    signal_name: str
    entry_time: datetime
    entry_price: float
    exit_time: datetime | None
    exit_price: float | None
    outcome: str | None
    realized_r: float | None
    pnl_pct: float | None


class StrategyTradesResponse(BaseModel):
    items: list[StrategyTrade]
    total: int


class StrategyBacktestSummary(BaseModel):
    id: int
    strategy_id: str
    strategy_kind: str
    strategy_name: str
    symbol: str
    timeframe: str
    trend_tf: str | None
    start_date: str
    end_date: str
    total_return_pct: float
    annualized_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    win_rate_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    buy_hold_return_pct: float
    created_at: datetime


class StrategyBacktestsResponse(BaseModel):
    items: list[StrategyBacktestSummary]
    total: int


class StrategyBacktestTrade(BaseModel):
    entry_time: int
    entry_price: float
    exit_time: int
    exit_price: float
    pnl_pct: float
    pnl_amount: float | None = None
    max_profit_pct: float
    max_loss_pct: float
    signal_weight: int | None = None
    signal_names: list[str] = []
    direction: str | None = None
    exit_reason: str | None = None


class StrategyBacktestTradesResponse(BaseModel):
    items: list[StrategyBacktestTrade]
    total: int


StrategyCard.model_rebuild()
