"""实验执行器：spec + 行情 + 指标注册表 → 交易、净值、指标。

撮合口径（刻意保守，避免回测幻觉）：

- 入场：信号确认后**下一根开盘**；
- 止损/目标：按入场时 ATR × ``stop_atr`` 设初始风险，盘中触及即成交；
- 同一根同时触及止损与目标 → 按**止损**计（``ambiguous``）；
- 指标离场：确认那根**收盘**成交；
- 到期：第 ``time_bars`` 根收盘平仓；
- 成本：双边 ``fee_bps + slippage_bps``；
- 仓位：每笔风险 ``risk_pct`` × 当前权益，权益逐笔复利。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from app.indicator_core import Event
from app.indicator_core.series import atr_series
from app.indicator_core.stats import summarize

from .spec import ExperimentSpec


@dataclass
class Trade:
    symbol: str
    timeframe: str
    direction: str
    entry_time: int
    entry_price: float
    exit_time: int
    exit_price: float
    exit_reason: str
    r_multiple: float
    pnl: float
    equity_after: float
    bars_held: int
    entry_event_uid: str = ""
    exit_event_uid: str = ""
    censored: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "entry_time": self.entry_time,
            "entry_price": round(self.entry_price, 6),
            "exit_time": self.exit_time,
            "exit_price": round(self.exit_price, 6),
            "exit_reason": self.exit_reason,
            "r_multiple": round(self.r_multiple, 4),
            "pnl": round(self.pnl, 6),
            "equity_after": round(self.equity_after, 6),
            "bars_held": self.bars_held,
            "entry_event_uid": self.entry_event_uid,
            "exit_event_uid": self.exit_event_uid,
            "censored": self.censored,
        }


@dataclass
class ExperimentResult:
    trades: list[Trade] = field(default_factory=list)
    equity: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metrics": self.metrics,
            "trades": [trade.to_dict() for trade in self.trades],
            "equity": self.equity,
            "errors": self.errors,
        }


def run_experiment(
    spec: ExperimentSpec,
    data: dict[tuple[str, str], Sequence[dict[str, Any]]],
    registry: Any,
) -> ExperimentResult:
    result = ExperimentResult()
    equity = 1.0
    for symbol in spec.symbols:
        for timeframe in spec.timeframes:
            bars = list(data.get((symbol, timeframe), []))
            if len(bars) < 50:
                result.errors.append(
                    {"symbol": symbol, "timeframe": timeframe, "error": "bars < 50"}
                )
                continue
            try:
                trades = _run_single(spec, bars, symbol, timeframe, registry, starting_equity=equity)
            except Exception as exc:  # noqa: BLE001 - 单个组合失败不应拖垮整个实验
                result.errors.append(
                    {"symbol": symbol, "timeframe": timeframe, "error": str(exc)[:200]}
                )
                continue
            for trade in trades:
                equity = trade.equity_after
            result.trades.extend(trades)

    result.trades.sort(key=lambda trade: trade.exit_time)
    result.metrics = _metrics(result.trades)
    result.equity = _equity_curve(result.trades)
    return result


def _run_single(
    spec: ExperimentSpec,
    bars: Sequence[dict[str, Any]],
    symbol: str,
    timeframe: str,
    registry: Any,
    *,
    starting_equity: float,
) -> list[Trade]:
    times = [int(bar["time"]) for bar in bars]

    entry_sources = list(spec.entry_all) + list(spec.entry_any)
    entries_by_time = _events_by_time(
        registry, entry_sources, bars, symbol, timeframe
    )
    exit_sources = list(spec.exit_indicators)
    exits_by_time = _events_by_time(registry, exit_sources, bars, symbol, timeframe)

    atr = atr_series(bars, 14)[0].values
    cost_rate = (spec.fee_bps + spec.slippage_bps) / 10_000
    trades: list[Trade] = []
    equity = starting_equity
    i = 0
    while i < len(bars) - 1:
        event = _entry_signal(spec, entries_by_time.get(times[i], []))
        if event is None:
            i += 1
            continue

        entry_index = i + 1
        entry_price = float(bars[entry_index]["open"])
        atr_value = atr[entry_index] or atr[i]
        if not atr_value or atr_value <= 0 or entry_price <= 0:
            i += 1
            continue

        direction = event.direction
        long_side = direction == "long"
        risk_per_unit = spec.stop_atr * float(atr_value)
        stop_price = entry_price - risk_per_unit if long_side else entry_price + risk_per_unit
        target_price = (
            entry_price + spec.target_r * risk_per_unit
            if long_side
            else entry_price - spec.target_r * risk_per_unit
        )

        exit_index: int | None = None
        exit_price = float(bars[entry_index]["close"])
        exit_reason = "eod"
        exit_uid = ""
        censored = True
        last_index = min(len(bars) - 1, entry_index + spec.time_bars)
        for j in range(entry_index, last_index + 1):
            bar = bars[j]
            high, low, close = float(bar["high"]), float(bar["low"]), float(bar["close"])
            hit_target = high >= target_price if long_side else low <= target_price
            hit_stop = low <= stop_price if long_side else high >= stop_price
            if hit_stop:
                exit_index, exit_price, exit_reason, censored = j, stop_price, "stop", False
                break
            if hit_target:
                exit_index, exit_price, exit_reason, censored = j, target_price, "target", False
                break
            opposite = [
                item
                for item in exits_by_time.get(int(bar["time"]), [])
                if item.direction != direction
            ]
            if opposite:
                exit_index, exit_price, exit_reason, censored = j, close, "signal", False
                exit_uid = opposite[0].uid
                break
            if j == entry_index + spec.time_bars:
                exit_index, exit_price, exit_reason, censored = j, close, "time", False
                break
            if j == len(bars) - 1:
                exit_index, exit_price, exit_reason = j, close, "eod"

        assert exit_index is not None
        entry_eff = entry_price * (1 + cost_rate) if long_side else entry_price * (1 - cost_rate)
        exit_eff = exit_price * (1 - cost_rate) if long_side else exit_price * (1 + cost_rate)
        risk_amount = equity * spec.risk_pct / 100
        qty = risk_amount / risk_per_unit
        pnl = qty * (exit_eff - entry_eff) if long_side else qty * (entry_eff - exit_eff)
        equity += pnl
        # R 倍数按「含成本」的有效价计算，保证 expectancy 与 pnl 同口径
        r_multiple = (
            (exit_eff - entry_eff) / risk_per_unit
            if long_side
            else (entry_eff - exit_eff) / risk_per_unit
        )
        trades.append(
            Trade(
                symbol=symbol.upper(),
                timeframe=timeframe,
                direction=direction,
                entry_time=times[entry_index],
                entry_price=entry_price,
                exit_time=times[exit_index],
                exit_price=exit_price,
                exit_reason=exit_reason,
                r_multiple=r_multiple,
                pnl=pnl,
                equity_after=equity,
                bars_held=exit_index - entry_index,
                entry_event_uid=event.uid,
                exit_event_uid=exit_uid,
                censored=censored,
            )
        )
        i = max(i + 1, exit_index + 1)
    return trades


def _events_by_time(
    registry: Any,
    sources: Iterable[tuple[str, dict[str, Any]]],
    bars: Sequence[dict[str, Any]],
    symbol: str,
    timeframe: str,
) -> dict[int, list[Event]]:
    out: dict[int, list[Event]] = {}
    for indicator_id, params in sources:
        for event in registry.run(indicator_id, bars, symbol, timeframe, params):
            out.setdefault(event.confirmed_at, []).append(event)
    return out


def _entry_signal(spec: ExperimentSpec, events: Sequence[Event]) -> Event | None:
    if not events:
        return None
    if spec.entry_all:
        by_indicator: dict[str, Event] = {}
        for event in events:
            by_indicator.setdefault(event.indicator_id, event)
        missing = [indicator for indicator, _ in spec.entry_all if indicator not in by_indicator]
        if missing:
            return None
        chosen = [by_indicator[indicator] for indicator, _ in spec.entry_all]
        if len({item.direction for item in chosen}) != 1:
            return None
        return chosen[0]
    if spec.entry_any:
        directions = {event.direction for event in events}
        return events[0] if len(directions) == 1 else None
    return None


def _metrics(trades: Sequence[Trade]) -> dict[str, Any]:
    summary = summarize(trades).to_dict()
    if not trades:
        return {**summary, "total_return_pct": 0.0, "max_drawdown_pct": 0.0}
    equity = 1.0
    peak = 1.0
    worst = 0.0
    returns: list[float] = []
    for trade in trades:
        previous = equity
        equity = trade.equity_after
        returns.append((equity - previous) / previous if previous else 0.0)
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1)
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / len(returns)
    sharpe = mean / math.sqrt(variance) * math.sqrt(len(returns)) if variance > 0 else 0.0
    return {
        **summary,
        "total_return_pct": round((equity - 1) * 100, 4),
        "max_drawdown_pct": round(abs(worst) * 100, 4),
        "sharpe_trade": round(sharpe, 3),
        "avg_bars_held": round(sum(t.bars_held for t in trades) / len(trades), 2),
    }


def _equity_curve(trades: Sequence[Trade]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    peak = 1.0
    for trade in trades:
        peak = max(peak, trade.equity_after)
        points.append(
            {
                "time": trade.exit_time,
                "equity": round(trade.equity_after, 6),
                "drawdown_pct": round((trade.equity_after / peak - 1) * 100, 4),
            }
        )
    return points
