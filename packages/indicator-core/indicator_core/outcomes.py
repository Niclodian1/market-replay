"""事件级结果标注：把 ``Event`` 变成一次可统计的交易（Outcome）。

口径（与设计稿 §8 决策 2 一致）：

- 入场：``confirmed_at`` 那根 K 线的**收盘价**（事件价只作为兜底）；
- 止损：入场前 ``stop_lookback`` 根的结构低点（做空为结构高点），
  结构不可用时退回 ``1 × ATR(14)``；
- 目标：``target_r`` 倍风险（默认 2R）；
- 期限：``horizon`` 根（默认 20），到期按收盘平仓；
- 首触判定；同一根同时触及止损与目标时**按止损计**（保守），并标记 ``ambiguous``；
- 数据不足以走完期限时标记 ``censored``，统计时可选剔除。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .series import atr_series
from .spec import Event


@dataclass(frozen=True)
class Outcome:
    event_uid: str
    indicator_id: str
    symbol: str
    timeframe: str
    direction: str
    entry_time: int
    entry_price: float
    stop_price: float
    target_price: float
    exit_time: int | None
    exit_price: float | None
    exit_reason: str  # target | stop | time | ambiguous | censor
    r_multiple: float | None
    mfe_r: float | None
    mae_r: float | None
    bars_held: int
    censored: bool = False

    @property
    def won(self) -> bool:
        return self.exit_reason == "target"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_uid": self.event_uid,
            "indicator_id": self.indicator_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "entry_time": self.entry_time,
            "entry_price": round(self.entry_price, 6),
            "stop_price": round(self.stop_price, 6),
            "target_price": round(self.target_price, 6),
            "exit_time": self.exit_time,
            "exit_price": None if self.exit_price is None else round(self.exit_price, 6),
            "exit_reason": self.exit_reason,
            "r_multiple": None if self.r_multiple is None else round(self.r_multiple, 4),
            "mfe_r": None if self.mfe_r is None else round(self.mfe_r, 4),
            "mae_r": None if self.mae_r is None else round(self.mae_r, 4),
            "bars_held": self.bars_held,
            "censored": self.censored,
            "won": self.won,
        }


def label_events(
    events: Iterable[Event],
    bars: Sequence[dict[str, Any]],
    *,
    horizon: int = 20,
    target_r: float = 2.0,
    stop_lookback: int = 20,
    atr_period: int = 14,
    include_censored: bool = True,
) -> list[Outcome]:
    if horizon <= 0:
        raise ValueError("horizon 必须为正整数")
    if target_r <= 0:
        raise ValueError("target_r 必须为正数")
    bar_list = list(bars)
    if not bar_list:
        return []
    index_by_time = {int(bar["time"]): i for i, bar in enumerate(bar_list)}
    atr_values = atr_series(bar_list, atr_period)[0].values
    outcomes: list[Outcome] = []

    for event in events:
        entry_index = index_by_time.get(int(event.confirmed_at))
        if entry_index is None:
            entry_index = _first_index_at_or_after(bar_list, int(event.confirmed_at))
        if entry_index is None:
            continue
        outcome = _label_one(
            event,
            bar_list,
            entry_index,
            atr_values,
            horizon=horizon,
            target_r=target_r,
            stop_lookback=stop_lookback,
        )
        if outcome is None:
            continue
        if outcome.censored and not include_censored:
            continue
        outcomes.append(outcome)
    return outcomes


def _first_index_at_or_after(bars: Sequence[dict[str, Any]], timestamp: int) -> int | None:
    for i, bar in enumerate(bars):
        if int(bar["time"]) >= timestamp:
            return i
    return None


def _label_one(
    event: Event,
    bars: Sequence[dict[str, Any]],
    entry_index: int,
    atr_values: Sequence[float | None],
    *,
    horizon: int,
    target_r: float,
    stop_lookback: int,
) -> Outcome | None:
    entry_bar = bars[entry_index]
    entry_price = float(entry_bar["close"])
    if entry_price <= 0:
        return None
    atr = atr_values[entry_index]
    long_side = event.direction == "long"

    lookback = bars[max(0, entry_index - stop_lookback) : entry_index]
    if long_side:
        structural = min((float(b["low"]) for b in lookback), default=None)
    else:
        structural = max((float(b["high"]) for b in lookback), default=None)

    risk: float | None = None
    if structural is not None:
        candidate = entry_price - structural if long_side else structural - entry_price
        if candidate > 0:
            risk = candidate
    if risk is None and atr is not None and atr > 0:
        risk = float(atr)
    if risk is None or risk <= 0:
        return None

    stop_price = entry_price - risk if long_side else entry_price + risk
    target_price = entry_price + target_r * risk if long_side else entry_price - target_r * risk

    mfe = mae = 0.0
    exit_index: int | None = None
    exit_price: float | None = None
    exit_reason = "censor"
    censored = False

    horizon_end = entry_index + horizon
    last_index = min(len(bars) - 1, horizon_end)
    for i in range(entry_index + 1, last_index + 1):
        bar = bars[i]
        high, low, close = float(bar["high"]), float(bar["low"]), float(bar["close"])
        favorable = (high - entry_price) / risk if long_side else (entry_price - low) / risk
        adverse = (low - entry_price) / risk if long_side else (entry_price - high) / risk
        mfe = max(mfe, favorable)
        mae = min(mae, adverse)

        hit_target = high >= target_price if long_side else low <= target_price
        hit_stop = low <= stop_price if long_side else high >= stop_price
        if hit_target and hit_stop:
            exit_index, exit_price, exit_reason = i, stop_price, "ambiguous"
            break
        if hit_stop:
            exit_index, exit_price, exit_reason = i, stop_price, "stop"
            break
        if hit_target:
            exit_index, exit_price, exit_reason = i, target_price, "target"
            break
        if i == last_index:
            exit_index, exit_price = i, close
            if last_index < horizon_end:
                exit_reason, censored = "censor", True
            else:
                exit_reason = "time"

    if exit_index is None:
        censored = True
        exit_index = last_index
        exit_price = float(bars[last_index]["close"])
        exit_reason = "censor"

    r_multiple = (
        (exit_price - entry_price) / risk
        if long_side
        else (entry_price - exit_price) / risk
    )
    return Outcome(
        event_uid=event.uid,
        indicator_id=event.indicator_id,
        symbol=event.symbol,
        timeframe=event.timeframe,
        direction=event.direction,
        entry_time=int(entry_bar["time"]),
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
        exit_time=int(bars[exit_index]["time"]),
        exit_price=exit_price,
        exit_reason=exit_reason,
        r_multiple=r_multiple,
        mfe_r=mfe,
        mae_r=mae,
        bars_held=exit_index - entry_index,
        censored=censored,
    )
