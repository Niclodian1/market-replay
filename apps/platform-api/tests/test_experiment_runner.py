"""实验内核：spec 校验与撮合口径。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core import chanlun_service
from app.core.indicator_provider import get_registry
from app.experiment_core import ExperimentSpec, parse_spec, run_experiment, validate_spec
from app.indicator_core import Event

PARQUET_1D = Path(__file__).resolve().parents[3] / "data" / "crypto" / "BTC_USD_1day.parquet"
DAY = 86_400
T0 = 1_700_000_000


def _spec(**overrides) -> dict:
    base = {
        "schema_version": "exp1",
        "name": "unit",
        "universe": {"symbols": ["BTC-USD"], "timeframes": ["1day"]},
        "entry": {"all": [{"indicator": "test.entry"}]},
        "exit": {"any": [{"type": "stop", "atr": 1.5}, {"type": "target", "r": 2.0}, {"type": "time", "bars": 20}]},
        "sizing": {"mode": "fixed_risk", "risk_pct": 1.0},
        "costs": {"fee_bps": 0, "slippage_bps": 0},
    }
    base.update(overrides)
    return base


def _bars(n: int = 60) -> list[dict]:
    bars = []
    for i in range(n):
        close = 100.0 + i
        open_ = close - 0.5
        bars.append(
            {
                "time": T0 + i * DAY,
                "open": open_,
                "high": close + 0.6,
                "low": open_ - 0.6,
                "close": close,
                "volume": 10.0,
            }
        )
    return bars


def _entry_event(index: int, direction: str = "long") -> Event:
    return Event(
        indicator_id="test.entry",
        symbol="BTC-USD",
        timeframe="1day",
        bar_time=T0 + index * DAY,
        confirmed_at=T0 + index * DAY,
        direction=direction,
        price=100.0 + index,
    )


class FakeRegistry:
    def __init__(self, events: dict[str, list[Event]]) -> None:
        self._events = events

    def run(self, indicator_id, bars, symbol, timeframe, params=None):
        return list(self._events.get(indicator_id, []))


def test_spec_validation_and_defaults() -> None:
    validate_spec(_spec())
    parsed = parse_spec(_spec())
    assert parsed.name == "unit"
    assert parsed.stop_atr == 1.5 and parsed.target_r == 2.0 and parsed.time_bars == 20
    assert parsed.spec_hash

    minimal = {
        "name": "min",
        "universe": {"symbols": ["BTC-USD"], "timeframes": ["1day"]},
        "entry": {"all": [{"indicator": "ma.breakout"}]},
        "exit": {"any": [{"indicator": "chanlun.sell1"}]},
    }
    full = parse_spec(minimal)
    assert full.stop_atr == 1.5 and full.time_bars == 20  # 自动补默认出场

    with pytest.raises(Exception):
        parse_spec({"name": "broken"})
    with pytest.raises(Exception):
        parse_spec(_spec(entry={}))


def test_runner_enters_next_open_and_hits_target() -> None:
    bars = _bars()
    registry = FakeRegistry({"test.entry": [_entry_event(30)]})
    result = run_experiment(parse_spec(_spec()), {("BTC-USD", "1day"): bars}, registry)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_time == bars[31]["time"]
    assert trade.exit_reason == "target"
    assert trade.r_multiple == pytest.approx(2.0, abs=0.01)
    assert trade.pnl > 0
    assert result.metrics["n"] == 1
    assert result.metrics["win_rate"] == 1.0
    assert result.metrics["total_return_pct"] > 0


def test_runner_stops_out_when_price_falls() -> None:
    bars = _bars()
    for i in range(32, len(bars)):
        bars[i] = {
            "time": bars[i]["time"],
            "open": bars[i - 1]["close"],
            "high": bars[i - 1]["close"] + 0.2,
            "low": bars[i - 1]["close"] - 3.0,
            "close": bars[i - 1]["close"] - 2.5,
            "volume": 10.0,
        }
    registry = FakeRegistry({"test.entry": [_entry_event(30)]})
    result = run_experiment(parse_spec(_spec()), {("BTC-USD", "1day"): bars}, registry)
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "stop"
    assert result.trades[0].r_multiple == pytest.approx(-1.5, abs=0.01) or result.trades[0].r_multiple < 0


def test_costs_reduce_pnl_and_time_exit_works() -> None:
    bars = _bars()
    registry = FakeRegistry({"test.entry": [_entry_event(30)]})
    free = run_experiment(parse_spec(_spec()), {("BTC-USD", "1day"): bars}, registry)
    costly = run_experiment(
        parse_spec(_spec(costs={"fee_bps": 50, "slippage_bps": 50})),
        {("BTC-USD", "1day"): bars},
        registry,
    )
    assert costly.trades[0].pnl < free.trades[0].pnl

    flat = _bars()
    for i in range(31, len(flat)):
        flat[i] = {
            "time": flat[i]["time"],
            "open": 110.0,
            "high": 110.3,
            "low": 109.7,
            "close": 110.0,
            "volume": 10.0,
        }
    time_spec = parse_spec(
        _spec(exit={"any": [{"type": "time", "bars": 5}]})
    )
    time_result = run_experiment(time_spec, {("BTC-USD", "1day"): flat}, FakeRegistry({"test.entry": [_entry_event(30)]}))
    assert time_result.trades[0].exit_reason == "time"
    assert time_result.trades[0].bars_held == 5


def test_indicator_exit_uses_close() -> None:
    bars = _bars()
    for i in range(31, len(bars)):
        bars[i] = {
            "time": bars[i]["time"],
            "open": 130.0,
            "high": 130.3,
            "low": 129.7,
            "close": 130.0,
            "volume": 10.0,
        }
    exit_event = Event(
        indicator_id="test.exit",
        symbol="BTC-USD",
        timeframe="1day",
        bar_time=bars[35]["time"],
        confirmed_at=bars[35]["time"],
        direction="short",
        price=bars[35]["close"],
    )
    spec = parse_spec(
        _spec(exit={"any": [{"indicator": "test.exit"}, {"type": "time", "bars": 20}]})
    )
    result = run_experiment(
        spec,
        {("BTC-USD", "1day"): bars},
        FakeRegistry({"test.entry": [_entry_event(30)], "test.exit": [exit_event]}),
    )
    trade = result.trades[0]
    assert trade.exit_reason == "signal"
    assert trade.exit_time == bars[35]["time"]
    assert trade.exit_price == pytest.approx(bars[35]["close"])


@pytest.mark.skipif(not PARQUET_1D.exists(), reason="BTC 日线数据不可用")
def test_real_ma_breakout_experiment_runs() -> None:
    from app.core.market_data import read_local_bars

    bars = list(read_local_bars("BTC-USD", "1day"))
    spec: ExperimentSpec = parse_spec(
        {
            "name": "ma20 breakout",
            "universe": {"symbols": ["BTC-USD"], "timeframes": ["1day"]},
            "entry": {"all": [{"indicator": "ma.breakout", "params": {"periods": [20]}}]},
            "exit": {
                "any": [
                    {"type": "stop", "atr": 1.5},
                    {"type": "target", "r": 2.0},
                    {"type": "time", "bars": 20},
                ]
            },
        }
    )
    result = run_experiment(spec, {("BTC-USD", "1day"): bars}, get_registry())
    assert result.trades, result.errors
    assert result.metrics["n"] == len(result.trades)
    assert result.metrics["n"] >= 20
    assert 0 <= result.metrics["win_rate"] <= 1
    assert len(result.equity) == len(result.trades)
