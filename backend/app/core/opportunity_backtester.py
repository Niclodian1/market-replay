from dataclasses import dataclass

from app.core.edge_service import combo_hash, wilson_lower_bound


@dataclass(frozen=True)
class EdgeSnapshot:
    combo_hash: str
    sample_size: int
    wins: int
    win_rate: float
    avg_r: float
    expectancy: float
    wilson_lower: float


def summarize_trades(signals: list[str], trades: list[dict], combine_mode: str = "AND") -> EdgeSnapshot:
    sample_size = len(trades)
    wins = sum(1 for trade in trades if float(trade.get("realized_r", trade.get("pnl_r", 0))) > 0)
    total_r = sum(float(trade.get("realized_r", trade.get("pnl_r", 0))) for trade in trades)
    avg_r = total_r / sample_size if sample_size else 0.0
    win_rate = wins / sample_size if sample_size else 0.0
    losses = [abs(float(trade.get("realized_r", trade.get("pnl_r", 0)))) for trade in trades if float(trade.get("realized_r", trade.get("pnl_r", 0))) <= 0]
    avg_loss = sum(losses) / len(losses) if losses else 1.0
    expectancy = avg_r * win_rate - avg_loss * (1 - win_rate) if sample_size else 0.0
    return EdgeSnapshot(
        combo_hash=combo_hash(signals, combine_mode),
        sample_size=sample_size,
        wins=wins,
        win_rate=win_rate,
        avg_r=avg_r,
        expectancy=expectancy,
        wilson_lower=wilson_lower_bound(wins, sample_size),
    )
