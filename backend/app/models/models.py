from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, Text, Float, DateTime, ForeignKey, Integer, Boolean, BigInteger, UniqueConstraint, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MarketCandle(Base):
    __tablename__ = "market_candles"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "time", name="uq_market_candle"),)

    id:        Mapped[int]   = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol:    Mapped[str]   = mapped_column(String(30), nullable=False, index=True)
    timeframe: Mapped[str]   = mapped_column(String(10), nullable=False, index=True)
    time:      Mapped[int]   = mapped_column(BigInteger, nullable=False)
    open:      Mapped[float] = mapped_column(Float, nullable=False)
    high:      Mapped[float] = mapped_column(Float, nullable=False)
    low:       Mapped[float] = mapped_column(Float, nullable=False)
    close:     Mapped[float] = mapped_column(Float, nullable=False)
    volume:    Mapped[float] = mapped_column(Float, nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    watchlists: Mapped[list["Watchlist"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    replay_notes: Mapped[list["ReplayNote"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    alerts: Mapped[list["Alert"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Watchlist(Base):
    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(20), nullable=False)  # stock, etf, index, crypto
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="watchlists")


class ReplayNote(Base):
    __tablename__ = "replay_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    replay_start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    note_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    trend_view: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    support_level: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    resistance_level: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    entry_idea: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stop_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    take_profit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="replay_notes")


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    # price_above, price_below, rsi_above, rsi_below, ma_cross_up, ma_cross_down
    condition_type: Mapped[str] = mapped_column(String(30), nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False, default="1day")
    discord_webhook_url: Mapped[str] = mapped_column(Text, nullable=False)
    ma_period: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    repeating: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_triggered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    triggered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    user: Mapped["User"] = relationship(back_populates="alerts")


class SignalEvent(Base):
    """A raw signal firing used for forward outcome tracking."""
    __tablename__ = "signal_events"
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "signal_name", "direction", "fired_at_ts", name="uq_signal_event_fire"),
        Index("ix_signal_events_symbol_tf_name", "symbol", "timeframe", "signal_name"),
        Index("ix_signal_events_open", "outcome"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    signal_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    fired_at_ts: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    fired_price: Mapped[float] = mapped_column(Float, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    outcome: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    realized_r: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    closed_at_ts: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


class EdgeStat(Base):
    """Rolling edge ledger for a symbol/timeframe/combo/direction."""
    __tablename__ = "edge_stats"
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "combo_hash", "direction", name="uq_edge_combo"),
        Index("ix_edge_expectancy", "expectancy"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    combo_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    combo_spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    wins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    avg_r: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    expectancy: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sharpe_like: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    wilson_lower: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    window_start_ts: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    window_end_ts: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    opportunities: Mapped[list["Opportunity"]] = relationship(back_populates="edge_stat")


class Opportunity(Base):
    """A detected trade opportunity candidate."""
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    combo_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    confluence_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False, index=True)
    edge_stat_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("edge_stats.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    realized_r: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    edge_stat: Mapped[Optional["EdgeStat"]] = relationship(back_populates="opportunities")
    trade_plan: Mapped[Optional["TradePlan"]] = relationship(back_populates="opportunity", cascade="all, delete-orphan", uselist=False)


class TradePlan(Base):
    """Concrete entry, risk, and target plan for an opportunity."""
    __tablename__ = "trade_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    opportunity_id: Mapped[int] = mapped_column(Integer, ForeignKey("opportunities.id", ondelete="CASCADE"), unique=True, nullable=False)
    entry: Mapped[float] = mapped_column(Float, nullable=False)
    stop_loss: Mapped[float] = mapped_column(Float, nullable=False)
    take_profit: Mapped[float] = mapped_column(Float, nullable=False)
    rr_ratio: Mapped[float] = mapped_column(Float, default=2.0, nullable=False)
    size_r: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="", nullable=False)
    valid_until_ts: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    opportunity: Mapped["Opportunity"] = relationship(back_populates="trade_plan")


class RecommenderThreshold(Base):
    """Persisted recommender filters used by opportunity generation."""
    __tablename__ = "recommender_thresholds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    min_winrate: Mapped[float] = mapped_column(Float, default=0.55, nullable=False)
    min_sample: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    min_score: Mapped[float] = mapped_column(Float, default=0.6, nullable=False)
    max_open_per_symbol: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class StrategyBacktestRun(Base):
    """Persisted MTF backtest result for strategy-library reporting."""
    __tablename__ = "strategy_backtest_runs"
    __table_args__ = (
        Index("ix_strategy_backtests_strategy", "strategy_id", "created_at"),
        Index("ix_strategy_backtests_symbol_tf", "symbol", "timeframe"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    strategy_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    strategy_kind: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    strategy_name: Mapped[str] = mapped_column(String(160), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    trend_tf: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    start_date: Mapped[str] = mapped_column(String(10), nullable=False)
    end_date: Mapped[str] = mapped_column(String(10), nullable=False)
    entry_signals: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    entry_params: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    exit_rules: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    initial_capital: Mapped[float] = mapped_column(Float, nullable=False)
    total_return_pct: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    annualized_return_pct: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sharpe_ratio: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    win_rate_pct: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_trades: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    winning_trades: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    losing_trades: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    buy_hold_return_pct: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False, index=True)
