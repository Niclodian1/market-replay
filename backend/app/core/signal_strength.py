"""Deterministic signal-strength ratings shared by alert presentations."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SignalStrength:
    stars: int
    label: str

    @property
    def glyphs(self) -> str:
        return "★" * self.stars + "☆" * (5 - self.stars)


STRENGTH_LABELS = {
    1: "观察",
    2: "较弱",
    3: "有效",
    4: "强",
    5: "关键",
}

SIGNAL_TYPE_LABELS = {
    "ma_breakout": "MA突破",
    "trendline_breakout": "趋势线突破",
    "ma_retest": "MA回踩确认",
    "macd_extreme_divergence": "MACD柱极值背离",
    "convergence_breakout": "收敛结构突破",
    "chanlun_buy1": "缠论一买",
    "chanlun_buy2": "缠论二买",
    "chanlun_buy3": "缠论三买",
    "chanlun_sell1": "缠论一卖",
    "chanlun_sell2": "缠论二卖",
    "chanlun_sell3": "缠论三卖",
    "chanlun_beichi_buy": "缠论背驰买",
    "chanlun_beichi_sell": "缠论背驰卖",
}

_STANDARD_SIGNAL = {
    "15min": 2,
    "1h": 3,
    "4h": 4,
    "1day": 5,
}

_CONFIRMED_SIGNAL = {
    "15min": 3,
    "1h": 4,
    "4h": 5,
    "1day": 5,
}

_EVENT_STRENGTHS = {
    "macd_extreme_divergence": _CONFIRMED_SIGNAL,
    "ma_breakout": _STANDARD_SIGNAL,
    "trendline_breakout": _STANDARD_SIGNAL,
    "ma_retest": _CONFIRMED_SIGNAL,
    "convergence_breakout": _CONFIRMED_SIGNAL,
    "chanlun_buy1": _CONFIRMED_SIGNAL,
    "chanlun_buy2": _STANDARD_SIGNAL,
    "chanlun_buy3": _STANDARD_SIGNAL,
    "chanlun_sell1": _CONFIRMED_SIGNAL,
    "chanlun_sell2": _STANDARD_SIGNAL,
    "chanlun_sell3": _STANDARD_SIGNAL,
    "chanlun_beichi_buy": _CONFIRMED_SIGNAL,
    "chanlun_beichi_sell": _CONFIRMED_SIGNAL,
}


def evaluate_signal_strength(
    event_type: str,
    timeframe: str,
    severity: str | None = None,
) -> SignalStrength:
    """Rate attention priority without implying win rate or position size."""
    normalized_event = str(event_type or "").lower()
    normalized_timeframe = str(timeframe or "").lower()
    normalized_severity = str(severity or "").lower()

    matrix = _EVENT_STRENGTHS.get(normalized_event)
    if matrix is None:
        if normalized_severity == "watch":
            stars = 1
        elif normalized_severity == "confirmed":
            stars = _CONFIRMED_SIGNAL.get(normalized_timeframe, 3)
        else:
            stars = _STANDARD_SIGNAL.get(normalized_timeframe, 2)
    else:
        stars = matrix.get(normalized_timeframe, min(matrix.values()))

    stars = max(1, min(5, int(stars)))
    return SignalStrength(stars=stars, label=STRENGTH_LABELS[stars])


def signal_type_label(event_type: str, strategy: str | None = None) -> str:
    fallback = {
        "ma": "MA",
        "macd": "MACD",
        "structure": "结构",
    }.get(str(strategy or "").lower(), str(strategy or event_type or "信号"))
    return SIGNAL_TYPE_LABELS.get(str(event_type or "").lower(), fallback)
