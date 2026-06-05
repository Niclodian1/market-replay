import time
from typing import Any

from app.core.edge_service import combo_hash
from app.core.trade_plan import TradePlanDraft, build_atr_trade_plan
from app.models.models import EdgeStat


def confidence_score(edge: EdgeStat | None, confluence_score: float, signal_count: int) -> float:
    sample_factor = min((edge.sample_size if edge else 0) / 100, 1.0)
    wilson = edge.wilson_lower if edge else 0.45
    combo_bonus = min(signal_count / 4, 1.0) * 0.08
    score = 0.5 * wilson + 0.3 * confluence_score + 0.2 * sample_factor + combo_bonus
    return round(min(max(score, 0.0), 1.0), 4)


def explain_plan(
    *,
    symbol: str,
    timeframe: str,
    direction: str,
    signals: list[str],
    entry: float,
    atr: float | None = None,
    edge: EdgeStat | None = None,
) -> dict[str, Any]:
    hash_value = combo_hash(signals)
    confluence_score = min(0.45 + 0.15 * len(signals), 0.9)
    score = confidence_score(edge, confluence_score, len(signals))
    draft = build_atr_trade_plan(
        entry=entry,
        direction=direction,
        atr=atr,
        avg_r=edge.avg_r if edge else 2.0,
        valid_until_ts=int(time.time()) + 86400,
    )
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "direction": direction,
        "combo_hash": hash_value,
        "score": score,
        "trade_plan": plan_to_dict(draft),
    }


def plan_to_dict(plan: TradePlanDraft) -> dict[str, Any]:
    return {
        "entry": plan.entry,
        "stop_loss": plan.stop_loss,
        "take_profit": plan.take_profit,
        "rr_ratio": plan.rr_ratio,
        "size_r": plan.size_r,
        "rationale": plan.rationale,
        "valid_until_ts": plan.valid_until_ts,
    }
