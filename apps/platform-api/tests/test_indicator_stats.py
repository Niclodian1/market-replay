"""结果标注与统计口径测试（R3 的地基）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core import chanlun_service
from app.indicator_core import Event
from app.indicator_core.outcomes import label_events
from app.indicator_core.stats import equity_curve, summarize, wilson_interval

PARQUET_1D = Path(__file__).resolve().parents[3] / "data" / "crypto" / "BTC_USD_1day.parquet"
_requires_engine = pytest.mark.skipif(
    not PARQUET_1D.exists() or not chanlun_service.chanlun_available(),
    reason="fenxing/BTC 日线数据不可用",
)

T0 = 1_700_000_000
DAY = 86_400


def _bar(i: int, *, open_: float, high: float, low: float, close: float) -> dict:
    return {
        "time": T0 + i * DAY,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 10.0,
    }


def _long_event(confirmed_index: int = 1) -> Event:
    return Event(
        indicator_id="test.long",
        symbol="BTC-USD",
        timeframe="1day",
        bar_time=T0 + confirmed_index * DAY,
        confirmed_at=T0 + confirmed_index * DAY,
        direction="long",
        price=100.0,
    )


def _base_bars() -> list[dict]:
    return [
        _bar(0, open_=100, high=101, low=99, close=100),
        _bar(1, open_=100, high=101, low=99.5, close=100),  # 入场：收盘 100
    ]


def test_target_hit_gives_plus_two_r() -> None:
    bars = _base_bars() + [_bar(2, open_=100, high=104, low=99.5, close=103)]
    (outcome,) = label_events([_long_event()], bars)
    assert outcome.exit_reason == "target"
    assert outcome.exit_price == pytest.approx(102.0)
    assert outcome.r_multiple == pytest.approx(2.0)
    assert outcome.won is True
    assert outcome.censored is False


def test_stop_hit_gives_minus_one_r_and_mae() -> None:
    bars = _base_bars() + [_bar(2, open_=100, high=100.2, low=98, close=98.5)]
    (outcome,) = label_events([_long_event()], bars)
    assert outcome.exit_reason == "stop"
    assert outcome.r_multiple == pytest.approx(-1.0)
    assert outcome.mae_r == pytest.approx(-2.0)  # (98-100)/1
    assert outcome.won is False


def test_same_bar_stop_and_target_is_ambiguous_and_conservative() -> None:
    bars = _base_bars() + [_bar(2, open_=100, high=104, low=98, close=101)]
    (outcome,) = label_events([_long_event()], bars)
    assert outcome.exit_reason == "ambiguous"
    assert outcome.r_multiple == pytest.approx(-1.0)


def test_time_exit_uses_close() -> None:
    bars = _base_bars() + [
        _bar(i, open_=100, high=101, low=99.6, close=100.5) for i in range(2, 25)
    ]
    (outcome,) = label_events([_long_event()], bars, horizon=20)
    assert outcome.exit_reason == "time"
    assert outcome.bars_held == 20
    assert outcome.r_multiple == pytest.approx(0.5)


def test_censor_when_data_ends_before_horizon() -> None:
    bars = _base_bars() + [
        _bar(i, open_=100, high=101, low=99.6, close=100.2) for i in range(2, 8)
    ]
    (outcome,) = label_events([_long_event()], bars, horizon=20)
    assert outcome.censored is True
    assert outcome.exit_reason == "censor"


def test_short_side_mirrors_long() -> None:
    bars = [
        _bar(0, open_=100, high=101, low=99, close=100),
        _bar(1, open_=100, high=100.5, low=99, close=100),
        _bar(2, open_=100, high=100.2, low=96, close=97),
    ]
    event = Event(
        indicator_id="test.short",
        symbol="BTC-USD",
        timeframe="1day",
        bar_time=T0 + DAY,
        confirmed_at=T0 + DAY,
        direction="short",
        price=100.0,
    )
    (outcome,) = label_events([event], bars)
    assert outcome.exit_reason == "target"
    assert outcome.r_multiple == pytest.approx(2.0)


def test_summarize_reports_expectancy_wilson_and_drawdown() -> None:
    bars = [
        _bar(0, open_=100, high=101, low=99, close=100),
        _bar(1, open_=100, high=101, low=99.5, close=100),
    ]
    target = bars + [_bar(2, open_=100, high=104, low=99.5, close=103)]
    stop = bars + [_bar(2, open_=100, high=100.2, low=98, close=98.5)]

    outcomes = label_events([_long_event()], target) + label_events([_long_event()], stop)
    summary = summarize(outcomes, min_sample=2)
    assert summary.n == 2
    assert summary.wins == 1 and summary.losses == 1
    assert summary.win_rate == pytest.approx(0.5)
    assert summary.expectancy_r == pytest.approx(0.5)
    assert summary.profit_factor == pytest.approx(2.0)
    assert 0 < summary.wilson_low < 0.5 < summary.wilson_high < 1
    assert summary.max_consecutive_losses >= 1
    assert summary.insufficient is False

    curve = equity_curve(outcomes)
    assert curve[-1]["r"] == pytest.approx(1.0)
    assert curve[-1]["drawdown_r"] <= 0


def test_wilson_interval_edges() -> None:
    assert wilson_interval(0, 0) == (0.0, 0.0)
    low, high = wilson_interval(30, 30)
    assert low > 0.8 and high == 1.0
    low, high = wilson_interval(0, 30)
    assert low == 0.0 and high < 0.2


@_requires_engine
def test_stats_preview_endpoint_matches_manual_labeling() -> None:
    import pandas as pd

    from app.api.stats_api import CandleIn, StatsRequest, stats_preview

    df = pd.read_parquet(PARQUET_1D).sort_values("time").reset_index(drop=True).tail(800)
    candles = [
        CandleIn(
            time=int(row.time),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=float(row.volume),
        )
        for row in df.itertuples()
    ]
    payload = stats_preview(
        StatsRequest(
            symbol="BTC-USD",
            timeframe="1day",
            candles=candles,
            indicator_id="ma.breakout",
            params={"periods": [20]},
        )
    )
    assert payload["n_events"] > 0
    assert payload["n_outcomes"] > 0
    summary = payload["summary"]
    assert summary["n"] + summary["censored"] <= payload["n_outcomes"]
    assert 0 <= summary["win_rate"] <= 1
    assert "year" in payload["groups"] and "direction" in payload["groups"]
    assert len(payload["equity"]) == summary["n"]
