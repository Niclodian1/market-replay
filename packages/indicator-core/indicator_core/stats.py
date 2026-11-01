"""结果聚合：胜率、期望 R、Wilson 区间、盈亏比、回撤。

刻意只用标准库 —— 这些数字要出现在 API、飞书卡片、前端卡片三处，
口径必须完全一致，所以只有这一份实现。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from .outcomes import Outcome

OutcomeLike = Outcome | Mapping[str, Any]


def _get(item: OutcomeLike, field: str) -> Any:
    return item[field] if isinstance(item, Mapping) else getattr(item, field)

DEFAULT_MIN_SAMPLE = 30


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """二项比例的 Wilson 置信区间（小样本比正态近似稳）。"""
    if n <= 0:
        return (0.0, 0.0)
    phat = successes / n
    denominator = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denominator
    margin = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denominator
    return (max(0.0, center - margin), min(1.0, center + margin))


@dataclass
class Summary:
    n: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    expectancy_r: float = 0.0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    profit_factor: float | None = None
    max_consecutive_losses: int = 0
    max_drawdown_r: float = 0.0
    total_r: float = 0.0
    wilson_low: float = 0.0
    wilson_high: float = 0.0
    censored: int = 0
    ambiguous: int = 0
    insufficient: bool = False
    min_sample: int = DEFAULT_MIN_SAMPLE
    by_exit_reason: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 4),
            "expectancy_r": round(self.expectancy_r, 4),
            "avg_win_r": round(self.avg_win_r, 4),
            "avg_loss_r": round(self.avg_loss_r, 4),
            "profit_factor": None if self.profit_factor is None else round(self.profit_factor, 3),
            "max_consecutive_losses": self.max_consecutive_losses,
            "max_drawdown_r": round(self.max_drawdown_r, 4),
            "total_r": round(self.total_r, 4),
            "wilson_low": round(self.wilson_low, 4),
            "wilson_high": round(self.wilson_high, 4),
            "censored": self.censored,
            "ambiguous": self.ambiguous,
            "insufficient": self.insufficient,
            "min_sample": self.min_sample,
            "by_exit_reason": dict(self.by_exit_reason),
        }


def summarize(
    outcomes: Iterable[OutcomeLike],
    *,
    exclude_censored: bool = True,
    min_sample: int = DEFAULT_MIN_SAMPLE,
) -> Summary:
    items = list(outcomes)
    censored = sum(1 for item in items if _get(item, "censored"))
    usable = [
        item
        for item in items
        if not (exclude_censored and _get(item, "censored"))
        and _get(item, "r_multiple") is not None
    ]
    summary = Summary(censored=censored, min_sample=min_sample)
    if not usable:
        summary.insufficient = True
        return summary

    r_values = [float(_get(item, "r_multiple") or 0.0) for item in usable]
    wins = [value for value in r_values if value > 0]
    losses = [value for value in r_values if value <= 0]
    summary.n = len(usable)
    summary.wins = len(wins)
    summary.losses = len(losses)
    summary.win_rate = summary.wins / summary.n
    summary.expectancy_r = sum(r_values) / summary.n
    summary.total_r = sum(r_values)
    summary.avg_win_r = sum(wins) / len(wins) if wins else 0.0
    summary.avg_loss_r = sum(losses) / len(losses) if losses else 0.0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    summary.profit_factor = gross_win / gross_loss if gross_loss > 0 else None
    summary.ambiguous = sum(
        1 for item in usable if _get(item, "exit_reason") == "ambiguous"
    )
    summary.by_exit_reason = _count_by(usable, lambda item: str(_get(item, "exit_reason")))
    summary.max_consecutive_losses = _max_consecutive(r_values, lambda v: v <= 0)
    summary.max_drawdown_r = _max_drawdown(r_values)
    summary.wilson_low, summary.wilson_high = wilson_interval(summary.wins, summary.n)
    summary.insufficient = summary.n < min_sample
    return summary


def equity_curve(outcomes: Iterable[OutcomeLike]) -> list[dict[str, Any]]:
    """累计 R 曲线（按入场时间排序），用于前端净值图与回撤。"""
    items = sorted(
        (
            item
            for item in outcomes
            if not _get(item, "censored") and _get(item, "r_multiple") is not None
        ),
        key=lambda item: _get(item, "entry_time"),
    )
    points: list[dict[str, Any]] = []
    cumulative = 0.0
    peak = 0.0
    for item in items:
        cumulative += float(_get(item, "r_multiple") or 0.0)
        peak = max(peak, cumulative)
        points.append(
            {
                "time": _get(item, "entry_time"),
                "r": round(cumulative, 4),
                "drawdown_r": round(cumulative - peak, 4),
            }
        )
    return points


def group_summaries(
    outcomes: Sequence[OutcomeLike],
    key: Callable[[OutcomeLike], str],
    *,
    exclude_censored: bool = True,
) -> list[dict[str, Any]]:
    buckets: dict[str, list[OutcomeLike]] = {}
    for item in outcomes:
        buckets.setdefault(key(item), []).append(item)
    rows = [
        {"key": bucket, **summarize(items, exclude_censored=exclude_censored).to_dict()}
        for bucket, items in buckets.items()
    ]
    rows.sort(key=lambda row: (-row["n"], row["key"]))
    return rows


def _count_by(items: Sequence[OutcomeLike], key: Callable[[OutcomeLike], str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = key(item)
        counts[value] = counts.get(value, 0) + 1
    return counts


def _max_consecutive(values: Sequence[float], predicate: Callable[[float], bool]) -> int:
    best = current = 0
    for value in values:
        if predicate(value):
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _max_drawdown(values: Sequence[float]) -> float:
    cumulative = peak = 0.0
    worst = 0.0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        worst = min(worst, cumulative - peak)
    return abs(worst)
