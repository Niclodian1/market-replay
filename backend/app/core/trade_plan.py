from dataclasses import dataclass


@dataclass(frozen=True)
class TradePlanDraft:
    entry: float
    stop_loss: float
    take_profit: float
    rr_ratio: float
    size_r: float
    rationale: str
    valid_until_ts: int


def build_atr_trade_plan(
    *,
    entry: float,
    direction: str,
    atr: float | None = None,
    avg_r: float = 2.0,
    valid_until_ts: int = 0,
) -> TradePlanDraft:
    stop_distance = max(atr or entry * 0.015, entry * 0.003)
    reward_multiple = min(max(avg_r if avg_r > 0 else 2.0, 1.2), 3.0)
    if direction == "short":
        stop_loss = entry + stop_distance
        take_profit = entry - stop_distance * reward_multiple
    else:
        stop_loss = entry - stop_distance
        take_profit = entry + stop_distance * reward_multiple
    return TradePlanDraft(
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        rr_ratio=reward_multiple,
        size_r=1.0,
        rationale=f"ATR/default risk distance {stop_distance:.4f}; target uses {reward_multiple:.2f}R from edge snapshot.",
        valid_until_ts=valid_until_ts,
    )
