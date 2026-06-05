from dataclasses import dataclass
from typing import Any

from app.core.edge_service import combo_hash
from app.core.signal_registry import SignalHit


@dataclass(frozen=True)
class ConfluenceResult:
    passed: bool
    direction: str | None
    score: float
    combo_hash: str | None
    payload: dict[str, Any]


def evaluate_confluence(hits: list[SignalHit], min_same_direction: int = 2) -> ConfluenceResult:
    grouped: dict[str, list[SignalHit]] = {"long": [], "short": []}
    for hit in hits:
        if hit.direction in grouped:
            grouped[hit.direction].append(hit)
    direction, aligned = max(grouped.items(), key=lambda item: len(item[1]))
    if len(aligned) < min_same_direction:
        return ConfluenceResult(False, None, 0.0, None, {"signals": [hit.__dict__ for hit in hits]})
    names = [hit.name for hit in aligned]
    avg_score = sum(hit.score for hit in aligned) / len(aligned)
    score = min(avg_score + min(len(aligned), 4) * 0.05, 1.0)
    return ConfluenceResult(
        passed=True,
        direction=direction,
        score=round(score, 4),
        combo_hash=combo_hash(names),
        payload={
            "signals": [hit.__dict__ for hit in aligned],
            "multi_tf_aligned": False,
        },
    )
