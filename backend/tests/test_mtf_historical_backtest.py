"""MTF 历史回放管道 —— 纯函数单测（2026-08-16）。

覆盖：
  - parse_binance_kline_csv：表头/无表头/毫秒转秒/坏行；
  - validate_closed_4h：唯一 / 严格 14400 连续 / OHLC / 全收盘；
  - aggregate_utc：1day 6 根、1week 42 根、头尾不完整丢弃、缺口桶丢弃；
  - classify_policies：A/B 策略与 week_relation（agree/disagree/sideways），
    日反向 A/B 都 false；
  - replay_monitor_events：逐根前缀（≤360）、留首个去重、未收盘尾巴不改旧结果；
  - compute_outcomes：long/short 镜像、ambiguous、neither、事件 bar 极值不计、
    next_open→end_close return、oracle（evaluate+label_decision）比对；
  - wilson_interval、verify_manifest（含 manifest 破坏 / sha256 摘要校验）。
"""
from __future__ import annotations

import csv
import hashlib
import gzip
import io
import json
import math
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from app.core.indicators import _atr
from app.core.market_state import (
    DEFAULT_MARKET_STATE_CONFIG,
    MarketState,
    MarketStateConfig,
    precompute_market_states,
)
from app.core.multi_timeframe import evaluate
from app.core.outcome_labeling import (
    TOUCH_AMBIGUOUS,
    TOUCH_NEGATIVE,
    TOUCH_NEITHER,
    TOUCH_POSITIVE,
    label_decision,
)
from app.core.signals import monitor_signals
from app.core.mtf_historical_backtest import (
    aggregate_utc,
    bisect_closed_index,
    build_event_rows,
    classify_policies,
    classify_with_reasons,
    closed_state_at,
    compute_outcomes,
    dedupe_decisions,
    decision_n_signals,
    fetch_binance_zip,
    merge_4h_batches,
    parse_binance_kline_csv,
    parse_zip_csv,
    read_gzip_json,
    replay_monitor_events,
    run_backtest_pipeline,
    sort_event_rows,
    summarize_events_csv,
    validate_closed_4h,
    verify_manifest,
    wilson_interval,
    write_events_csv,
    write_gzip_json_deterministic,
)

T0 = 1_704_067_200  # 2024-01-01T00:00:00Z（周一，UTC 日/周边界）
STEP4 = 14400

# 缩短 warmup 的 M1 配置（与 M2/M3A 测试一致，保证可 replay）
QUICK = MarketStateConfig(
    vol_percentile_lookback=10,
    vol_percentile_low=0.2,
    vol_percentile_high=0.8,
    vol_change_window=3,
    ma_fast=5,
    ma_slow=10,
    ma_tertiary=15,
    atr_period=14,
    adx_period=5,
    pivot=3,
)


def make_bars(n: int, step: int, *, drift: float = 0.008, wave: float = 0.02) -> list[dict]:
    out = []
    for i in range(n):
        close = 100.0 * (1 + drift * i) * (1 + wave * math.sin(i / 9.0))
        op = 100.0 * (1 + drift * max(0, i - 1)) * (1 + wave * math.sin((i - 1) / 9.0))
        out.append({
            "time": T0 + i * step,
            "open": round(op, 6),
            "high": round(max(op, close) * 1.012, 6),
            "low": round(min(op, close) * 0.988, 6),
            "close": round(close, 6),
            "volume": 1000 + (i * 37) % 11,
            "is_closed": True,
        })
    return out


def make_candles(n4: int = 700, *, drift1w: float = 0.008, drift1d: float = 0.008) -> dict[str, list[dict]]:
    return {
        "4h": make_bars(n4, STEP4),
        "1day": make_bars(n4 // 6 + 2, 86400, drift=drift1d),
        "1week": make_bars(n4 // 42 + 4, 604800, drift=drift1w),
    }


def as_of_4h_close(bar_index: int) -> int:
    return T0 + (bar_index + 1) * STEP4


def bar_at(t: int, entry: float, half: float = 1.0, *, volume: int = 1000,
           is_closed: bool = True) -> dict:
    return {
        "time": t,
        "open": round(entry, 6),
        "high": round(entry + half, 6),
        "low": round(entry - half, 6),
        "close": round(entry, 6),
        "volume": volume,
        "is_closed": is_closed,
    }


def plain_seq(event_idx: int = 200, *, entry: float = 100.0, half: float = 1.0,
              n_window: int = 40, pattern: list[tuple[float, float, float, float]] | None = None,
              extreme_event_bar: bool = False) -> tuple[list[dict], float]:
    """事件前横盘（TR=2*half → ATR=2*half）、事件 bar close=entry、
    其后 n_window 根 pattern 窗口（相对 entry 的 R=2*half 倍数）。"""
    bars = [bar_at(T0 + i * STEP4, entry, half) for i in range(event_idx + 1)]
    if extreme_event_bar:
        bars[event_idx] = {
            "time": bars[event_idx]["time"],
            "open": round(entry, 6),
            "high": round(entry + 40 * half, 6),
            "low": round(entry - 40 * half, 6),
            "close": round(entry, 6),
            "volume": 1000,
            "is_closed": True,
        }
        highs = [float(b["high"]) for b in bars]
        lows = [float(b["low"]) for b in bars]
        closes = [float(b["close"]) for b in bars]
        R = _atr(highs, lows, closes, 14)[event_idx]
    else:
        R = 2 * half  # 横盘 TR=2*half → Wilder ATR14=2*half
    if pattern is not None:
        for k in range(n_window):
            o, h, l, c = pattern[k % len(pattern)]
            bars.append({
                "time": T0 + (event_idx + 1 + k) * STEP4,
                "open": round(entry + o * R, 6),
                "high": round(entry + h * R, 6),
                "low": round(entry + l * R, 6),
                "close": round(entry + c * R, 6),
                "volume": 1000,
                "is_closed": True,
            })
    return bars, R


# 与 M3A 测试同口径的窗口 pattern（相对 entry 的 R 倍数）
PATTERN_A = [
    (0.1, 0.5, -0.3, 0.4), (0.3, 1.2, -0.1, 1.1), (0.0, 0.3, -0.2, 0.2),
    (0.0, 0.2, -0.4, 0.0), (0.0, 0.1, -0.5, -0.2),
]
PATTERN_C = [
    (0.0, 0.4, -0.5, -0.3), (0.0, 0.3, -0.4, 0.2), (0.0, 0.2, -0.3, 0.1),
    (0.0, 0.4, -0.5, 0.3), (0.0, 0.1, -0.4, -0.2),
]
PATTERN_AMB = [
    (0.0, 2.0, -2.0, 0.0), (0.0, 0.5, -0.5, 0.0), (0.0, 0.3, -0.3, 0.1),
    (0.0, 0.2, -0.2, 0.0), (0.0, 0.4, -0.4, 0.1),
]


def win(out: dict, w: int) -> dict:
    return next(x for x in out["windows"] if x["window"] == w)


def make_flat_with_breakout(n: int = 140, breakouts: list[int] | None = None) -> list[dict]:
    bars = [bar_at(T0 + i * STEP4, 100.0) for i in range(n)]
    for bi in breakouts or []:
        bars[bi] = {
            "time": T0 + bi * STEP4, "open": 100.0, "high": 112.0, "low": 99.0,
            "close": 111.0, "volume": 5000, "is_closed": True,
        }
    return bars


def _macd_buy_bars() -> list[dict]:
    """确定性合成 MACD 多头（buy）柱极值背离：深跌段 → 反弹段 → 带中途回弹的弱跌段，
    末根为当前段柱极值，detect_macd_events 在最后一根产出 direction='buy'。"""
    p = 100.0
    closes: list[float] = []
    for _ in range(60):
        closes.append(p); p *= 1.002      # 基线缓涨
    for _ in range(30):
        closes.append(p); p *= 0.99       # 深跌段（负柱极值）
    for _ in range(16):
        closes.append(p); p *= 1.012      # 反弹段（反向正柱）
    for _ in range(10):
        closes.append(p); p *= 0.995      # 弱跌段前段
    for _ in range(4):
        closes.append(p); p *= 1.006      # 中途小幅反弹 → 柱缩短（alert）
    for _ in range(6):
        closes.append(p); p *= 0.994      # 末段柱极值在最后一根
    return [{
        "time": T0 + i * STEP4,
        "open": round(c * 0.999, 6),
        "high": round(c * 1.01, 6),
        "low": round(c * 0.99, 6),
        "close": round(c, 6),
        "volume": 1000.0,
        "is_closed": True,
    } for i, c in enumerate(closes)]


def _ms(*, ready: bool = True, closed: bool = True, trend: str = "up") -> MarketState:
    return MarketState(
        schema_version="m1-v1", config_hash="x", bar_index=0, bar_time=0,
        ready=ready, is_closed=closed, trend_direction=trend,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  1. parse_binance_kline_csv
# ═══════════════════════════════════════════════════════════════════════════════

def _csv(header: bool = True, rows: list[str] | None = None,
         first_col: str = "open_time") -> bytes:
    head = (f"{first_col},open,high,low,close,volume,close_time,quote_volume,"
            f"count,taker_buy_volume,taker_buy_quote_volume,ignore\n")
    body = rows or [
        "1704067200000,100.0,101.0,99.0,100.5,1234.5,1704068640000,5,10,600.0,600.0,0",
        "1704068640000,100.5,102.0,100.0,101.5,888.0,1704070080000,6,11,400.0,400.0,0",
    ]
    return (head if header else "").encode() + "\n".join(body).encode()


def test_parse_skips_header_when_first_col_open_time():
    out = parse_binance_kline_csv(_csv())
    assert len(out) == 2  # 表头被跳过，两行数据都在
    assert out[0]["time"] == 1_704_067_200  # 毫秒 → 秒
    assert out[0]["open"] == 100.0
    assert out[0]["high"] == 101.0
    assert out[0]["low"] == 99.0
    assert out[0]["close"] == 100.5
    assert out[0]["volume"] == 1234.5
    assert out[0]["is_closed"] is True
    assert out[1]["time"] == 1_704_068_640
    assert out[1]["close"] == 101.5


def test_parse_no_header_keeps_first_row():
    out = parse_binance_kline_csv(_csv(header=False))
    assert len(out) == 2  # 无表头：首根不能丢
    assert out[0]["time"] == 1_704_067_200
    assert out[0]["close"] == 100.5


def test_parse_epoch_seconds_kept_as_is():
    out = parse_binance_kline_csv(b"1704067200,100,101,99,100.5,1234\n")
    assert out[0]["time"] == 1_704_067_200  # 已是秒，不除以 1000


def test_parse_header_other_first_col_raises():
    # 首列不是 open_time 的表头不能跳过 → 当作数据行解析失败
    with pytest.raises(ValueError):
        parse_binance_kline_csv(_csv(first_col="timestamp"))


def test_parse_short_row_raises():
    with pytest.raises(ValueError):
        parse_binance_kline_csv(b"1704067200,100,101,99\n")


def test_parse_bad_number_raises():
    with pytest.raises(ValueError):
        parse_binance_kline_csv(b"1704067200,abc,101,99,100.5,1234\n")


def test_parse_empty_returns_empty():
    assert parse_binance_kline_csv(b"") == []
    assert parse_binance_kline_csv(b"\n\n") == []


# ═══════════════════════════════════════════════════════════════════════════════
#  2. validate_closed_4h
# ═══════════════════════════════════════════════════════════════════════════════

def test_validate_ok():
    validate_closed_4h(make_bars(50, STEP4))  # 不抛


def test_validate_duplicate_time_raises():
    bars = make_bars(50, STEP4)
    bars[10] = dict(bars[10], time=bars[9]["time"])
    with pytest.raises(ValueError):
        validate_closed_4h(bars)


def test_validate_gap_raises():
    bars = make_bars(50, STEP4)
    bars[20] = dict(bars[20], time=bars[20]["time"] + 14400)  # 跳 2 根
    with pytest.raises(ValueError):
        validate_closed_4h(bars)


def test_validate_wrong_step_raises():
    bars = make_bars(50, STEP4)
    bars[10] = dict(bars[10], time=bars[10]["time"] + 3600)  # 非 14400 间隔
    with pytest.raises(ValueError):
        validate_closed_4h(bars)


def test_validate_bad_ohlc_raises():
    bars = make_bars(50, STEP4)
    bars[15] = dict(bars[15], high=90.0, low=110.0)  # high < low
    with pytest.raises(ValueError):
        validate_closed_4h(bars)


def test_validate_nonfinite_raises():
    bars = make_bars(50, STEP4)
    bars[15] = dict(bars[15], close=math.inf)
    with pytest.raises(ValueError):
        validate_closed_4h(bars)


def test_validate_unclosed_raises():
    bars = make_bars(50, STEP4)
    bars[30] = dict(bars[30], is_closed=False)
    with pytest.raises(ValueError):
        validate_closed_4h(bars)


def test_validate_missing_ohlc_raises():
    bars = make_bars(50, STEP4)
    del bars[25]["close"]
    with pytest.raises(ValueError):
        validate_closed_4h(bars)


def test_validate_bool_time_raises():
    bars = make_bars(50, STEP4)
    bars[10] = dict(bars[10], time=True)
    with pytest.raises(ValueError):
        validate_closed_4h(bars)


def test_validate_noninteger_time_raises():
    bars = make_bars(50, STEP4)
    bars[10] = dict(bars[10], time=bars[10]["time"] + 0.5)  # 非整数秒
    with pytest.raises(ValueError):
        validate_closed_4h(bars)


def test_validate_inf_time_raises():
    bars = make_bars(50, STEP4)
    bars[10] = dict(bars[10], time=math.inf)
    with pytest.raises(ValueError):
        validate_closed_4h(bars)


def test_validate_negative_volume_raises():
    bars = make_bars(50, STEP4)
    bars[15] = dict(bars[15], volume=-1.0)
    with pytest.raises(ValueError):
        validate_closed_4h(bars)


def test_validate_nonfinite_volume_raises():
    bars = make_bars(50, STEP4)
    bars[15] = dict(bars[15], volume=math.inf)
    with pytest.raises(ValueError):
        validate_closed_4h(bars)


def test_validate_bool_volume_raises():
    bars = make_bars(50, STEP4)
    bars[15] = dict(bars[15], volume=True)
    with pytest.raises(ValueError):
        validate_closed_4h(bars)


# ═══════════════════════════════════════════════════════════════════════════════
#  3. aggregate_utc
# ═══════════════════════════════════════════════════════════════════════════════

def test_aggregate_1day_full_buckets():
    bars = make_bars(12, STEP4)
    agg = aggregate_utc(bars, "1day")
    assert len(agg) == 2
    assert agg[0]["time"] == T0
    assert agg[1]["time"] == T0 + 86400
    assert agg[0]["open"] == bars[0]["open"]
    assert agg[0]["close"] == bars[5]["close"]
    assert agg[0]["high"] == max(b["high"] for b in bars[:6])
    assert agg[0]["low"] == min(b["low"] for b in bars[:6])
    assert agg[0]["volume"] == sum(b["volume"] for b in bars[:6])
    assert agg[0]["is_closed"] is True
    assert agg[1]["open"] == bars[6]["open"]
    assert agg[1]["close"] == bars[11]["close"]


def test_aggregate_1week_full_buckets():
    bars = make_bars(84, STEP4)
    agg = aggregate_utc(bars, "1week")
    assert len(agg) == 2
    assert agg[0]["time"] == T0  # 周一边界 00:00
    assert agg[1]["time"] == T0 + 604800
    assert agg[0]["high"] == max(b["high"] for b in bars[:42])
    assert agg[0]["low"] == min(b["low"] for b in bars[:42])
    assert agg[0]["volume"] == sum(b["volume"] for b in bars[:42])
    assert agg[1]["close"] == bars[83]["close"]


def test_aggregate_head_tail_incomplete_dropped():
    # 从 day0 第 3 根 4h 开始共 14 根：day0 4 根 / day1 6 根 / day2 4 根
    bars = [bar_at(T0 + i * STEP4, 100.0) for i in range(2, 16)]
    agg = aggregate_utc(bars, "1day")
    assert len(agg) == 1  # 头尾不完整桶丢弃
    assert agg[0]["time"] == T0 + 86400


def test_aggregate_week_midweek_start():
    # 周三 00:00 起 74 根：头周 30 根（5 天）丢、中周 42 根留、尾 2 根丢
    bars = [bar_at(T0 + (12 + i) * STEP4, 100.0) for i in range(74)]
    agg = aggregate_utc(bars, "1week")
    assert len(agg) == 1
    assert agg[0]["time"] == T0 + 604800


def test_aggregate_gap_bucket_dropped_not_crossed():
    # day0 完整 6 根、day1 缺 idx7（桶内缺口）→ day1 丢弃、day2 完整保留
    bars = [bar_at(T0 + i * STEP4, 100.0) for i in range(18) if i != 7]
    agg = aggregate_utc(bars, "1day")
    assert len(agg) == 2  # day0 与 day2，不跨缺口
    assert agg[0]["time"] == T0
    assert agg[1]["time"] == T0 + 2 * 86400


def test_aggregate_bad_timeframe_raises():
    with pytest.raises(ValueError):
        aggregate_utc(make_bars(12, STEP4), "1h")


# ═══════════════════════════════════════════════════════════════════════════════
#  4. classify_policies
# ═══════════════════════════════════════════════════════════════════════════════

def test_classify_agree_both():
    r = classify_policies(_ms(trend="up"), _ms(trend="up"), _ms(), "long")
    assert r["A"] is True and r["B"] is True
    assert r["week_relation"] == "agree"


def test_classify_disagree_only_b():
    r = classify_policies(_ms(trend="down"), _ms(trend="up"), _ms(), "long")
    assert r["A"] is False and r["B"] is True
    assert r["week_relation"] == "disagree"


def test_classify_sideways_only_b():
    r = classify_policies(_ms(trend="sideways"), _ms(trend="up"), _ms(), "long")
    assert r["A"] is False and r["B"] is True
    assert r["week_relation"] == "sideways"


def test_classify_unknown_week_sideways():
    r = classify_policies(_ms(trend="unknown"), _ms(trend="up"), _ms(), "long")
    assert r["A"] is False and r["B"] is True
    assert r["week_relation"] == "sideways"


def test_classify_day_opposite_kills_both():
    r = classify_policies(_ms(trend="up"), _ms(trend="down"), _ms(), "long")
    assert r["A"] is False and r["B"] is False  # 日反向 → A/B 都 false
    assert r["week_relation"] == "agree"


def test_classify_day_unknown_both_false():
    # 日线 unknown 无法映射 up/down → A/B 都 false（旧实现误让 A=True）
    r = classify_policies(_ms(trend="up"), _ms(trend="unknown"), _ms(), "long")
    assert r["A"] is False and r["B"] is False
    assert r["week_relation"] == "agree"


def test_classify_day_sideways_both_false():
    # 日线 sideways 无法映射 up/down → A/B 都 false
    r = classify_policies(_ms(trend="up"), _ms(trend="sideways"), _ms(), "long")
    assert r["A"] is False and r["B"] is False
    assert r["week_relation"] == "agree"


def test_classify_buy_treated_as_long():
    # MACD 事件方向为 buy → 归一为 long，周/日同向时 A/B 都 true
    r = classify_policies(_ms(trend="up"), _ms(trend="up"), _ms(), "buy")
    assert r["A"] is True and r["B"] is True
    assert r["week_relation"] == "agree"


def test_classify_sell_treated_as_short():
    r = classify_policies(_ms(trend="down"), _ms(trend="down"), _ms(), "sell")
    assert r["A"] is True and r["B"] is True
    assert r["week_relation"] == "agree"


def test_classify_buy_with_day_up_mismatch():
    # buy（long）但日线 down → 反向，A/B 都 false；周线 down 与 long 反向 → disagree
    r = classify_policies(_ms(trend="down"), _ms(trend="down"), _ms(), "buy")
    assert r["A"] is False and r["B"] is False
    assert r["week_relation"] == "disagree"


def test_classify_invalid_direction_raises():
    with pytest.raises(ValueError):
        classify_policies(_ms(trend="up"), _ms(trend="up"), _ms(), "longish")


def test_classify_week_not_ready():
    r = classify_policies(_ms(ready=False, trend="up"), _ms(trend="up"), _ms(), "long")
    assert r["A"] is False and r["B"] is False


def test_classify_week_unclosed():
    r = classify_policies(_ms(closed=False, trend="up"), _ms(trend="up"), _ms(), "long")
    assert r["A"] is False and r["B"] is False


def test_classify_day_not_ready_both_false():
    r = classify_policies(_ms(trend="up"), _ms(ready=False, trend="up"), _ms(), "long")
    assert r["A"] is False and r["B"] is False


def test_classify_trigger_not_ready():
    r = classify_policies(_ms(trend="up"), _ms(trend="up"), _ms(ready=False), "long")
    assert r["A"] is False and r["B"] is False


def test_classify_short_side():
    r = classify_policies(_ms(trend="down"), _ms(trend="down"), _ms(), "short")
    assert r["A"] is True and r["B"] is True
    assert r["week_relation"] == "agree"


# ═══════════════════════════════════════════════════════════════════════════════
#  5. replay_monitor_events
# ═══════════════════════════════════════════════════════════════════════════════

def test_replay_ma_breakout_dedupes_same_key():
    bars = make_flat_with_breakout(140, breakouts=[130])
    events = replay_monitor_events(bars, ["ma"], {})
    ma = [e for e in events
          if e["event_type"] == "ma_breakout" and e["direction"] == "long"]
    assert ma
    # 4 个 period 同 bar 突破 → 同 key（strategy,event_type,direction,signal_time）
    # → 只留首个
    assert len(ma) == 1
    assert ma[0]["signal_time"] == T0 + 130 * STEP4
    keys = {(e["strategy"], e["event_type"], e["direction"], e["signal_time"])
            for e in events}
    assert len(keys) == len(events)  # 全局无重复 key


def test_replay_keeps_early_events():
    bars = make_flat_with_breakout(220, breakouts=[130, 200])
    events = replay_monitor_events(bars, ["ma"], {})
    ts = {e["signal_time"] for e in events
          if e["event_type"] == "ma_breakout" and e["direction"] == "long"}
    assert {T0 + 130 * STEP4, T0 + 200 * STEP4} <= ts


def test_replay_unclosed_tail_does_not_change_old_results():
    bars = make_flat_with_breakout(140, breakouts=[130])
    e1 = replay_monitor_events(bars, ["ma"], {})
    # 未来尾巴：大阳线（会触发 detector）但显式未收盘 → 不得进入结果
    tail = [{
        "time": T0 + 140 * STEP4, "open": 100.0, "high": 115.0, "low": 98.0,
        "close": 114.0, "volume": 9000, "is_closed": False,
    }]
    e2 = replay_monitor_events(bars + tail, ["ma"], {})
    k1 = {(e["strategy"], e["event_type"], e["direction"], e["signal_time"])
          for e in e1}
    k2 = {(e["strategy"], e["event_type"], e["direction"], e["signal_time"])
          for e in e2}
    assert k1 == k2  # 尾巴不改变旧结果
    assert all(e["signal_time"] != T0 + 140 * STEP4 for e in e2)


def test_replay_prefix_never_exceeds_360(monkeypatch):
    bars = make_flat_with_breakout(500, breakouts=[130, 400])
    calls: list[int] = []
    real = monitor_signals.run_monitor_detectors

    def spy(candles, timeframe, strategies, parameters=None):
        calls.append(len(candles))
        return real(candles, timeframe, strategies, parameters)

    monkeypatch.setattr(monitor_signals, "run_monitor_detectors", spy)
    events = replay_monitor_events(bars, ["ma"], {})
    assert calls
    assert max(calls) <= 360  # 最近最多 360 根前缀
    assert events  # 事件仍被逐根扫出


def test_replay_too_short_no_events():
    bars = make_flat_with_breakout(100, breakouts=[90])  # 前缀 < 122 根
    assert replay_monitor_events(bars, ["ma"], {}) == []


def test_replay_cutoff_at_first_unclosed_no_ohlc_read():
    """第一个 is_closed=False 即 cutoff：不读其 OHLC（bar 只有 time/is_closed），
    不调用 detector；其后即使标 closed 也不可见。"""
    bars = make_flat_with_breakout(140, breakouts=[130])
    # bar140 未收盘且只有 time/is_closed（无 OHLC）；bar141 已收盘但会触发 detector
    tail = [
        {"time": T0 + 140 * STEP4, "is_closed": False},
        {"time": T0 + 141 * STEP4, "open": 100.0, "high": 115.0, "low": 98.0,
         "close": 114.0, "volume": 9000, "is_closed": True},
    ]
    events = replay_monitor_events(bars + tail, ["ma"], {})
    # 已收盘前缀事件逐字不变（与无尾巴时完全一致）
    assert events == replay_monitor_events(bars, ["ma"], {})
    # 未收盘 bar 与 cutoff 之后的已收盘 bar 都不可见
    assert all(e["signal_time"] not in (T0 + 140 * STEP4, T0 + 141 * STEP4)
               for e in events)


def test_replay_unclosed_tail_with_only_time_is_closed_ok():
    # 未收盘尾巴只有 time/is_closed → 不得 KeyError
    bars = make_flat_with_breakout(140, breakouts=[130])
    tail = [{"time": T0 + 140 * STEP4, "is_closed": False}] * 5
    events = replay_monitor_events(bars + tail, ["ma"], {})
    assert events == replay_monitor_events(bars, ["ma"], {})


def test_replay_macd_buy_event_end_to_end():
    """MACD 背离事件方向为 buy/sell：replay 产出后可直接喂 compute_outcomes，
    归一为 long/short，返回/计算同口径。"""
    bars = _macd_buy_bars()
    events = replay_monitor_events(bars, ["macd"], {})
    macd_events = [e for e in events if e["strategy"] == "macd"]
    assert macd_events, "合成 MACD buy 背离应产生事件"
    for ev in macd_events:
        assert ev["direction"] in ("buy", "sell", "long", "short")
        out = compute_outcomes(bars, {
            "signal_time": ev["signal_time"],
            "direction": ev["direction"],
            "reference_price": ev["reference_price"],
        })
        assert out["direction"] in ("long", "short")
        assert out["entry"] == pytest.approx(ev["reference_price"], rel=1e-9)


# ═══════════════════════════════════════════════════════════════════════════════
#  6. compute_outcomes
# ═══════════════════════════════════════════════════════════════════════════════

def test_outcomes_long_manual():
    bars, R = plain_seq(pattern=PATTERN_A)
    event = {"signal_time": T0 + 200 * STEP4, "direction": "long",
             "reference_price": 100.0}
    out = compute_outcomes(bars, event)
    assert out["entry"] == pytest.approx(100.0)
    assert out["atr_abs"] == pytest.approx(R)  # 横盘 ATR=2*half
    w = win(out, 5)
    assert w["matured"] is True
    assert w["mfe_r"] == pytest.approx(1.2, rel=1e-9)
    assert w["mae_r"] == pytest.approx(0.5, rel=1e-9)
    assert w["mfe_pct"] == pytest.approx(1.2 * R / 100.0, rel=1e-9)
    assert w["mae_pct"] == pytest.approx(0.5 * R / 100.0, rel=1e-9)
    assert w["first_touch"] == TOUCH_POSITIVE
    assert w["fake_breakout"] is False


def test_outcomes_short_mirror():
    bars, R = plain_seq(pattern=PATTERN_A)
    event = {"signal_time": T0 + 200 * STEP4, "direction": "short",
             "reference_price": 100.0}
    out = compute_outcomes(bars, event)
    w = win(out, 5)
    # short 镜像：MFE == long 的 MAE，MAE == long 的 MFE
    assert w["mfe_r"] == pytest.approx(0.5, rel=1e-9)
    assert w["mae_r"] == pytest.approx(1.2, rel=1e-9)
    assert w["first_touch"] == TOUCH_NEGATIVE
    assert w["fake_breakout"] is True


def test_outcomes_ambiguous_same_bar():
    bars, _ = plain_seq(pattern=PATTERN_AMB)
    event = {"signal_time": T0 + 200 * STEP4, "direction": "long",
             "reference_price": 100.0}
    w = win(compute_outcomes(bars, event), 5)
    assert w["first_touch"] == TOUCH_AMBIGUOUS
    assert w["fake_breakout"] is None
    assert w["mfe_r"] == pytest.approx(2.0, rel=1e-9)
    assert w["mae_r"] == pytest.approx(2.0, rel=1e-9)


def test_outcomes_neither():
    bars, _ = plain_seq(pattern=PATTERN_C)
    event = {"signal_time": T0 + 200 * STEP4, "direction": "long",
             "reference_price": 100.0}
    w = win(compute_outcomes(bars, event), 5)
    assert w["first_touch"] == TOUCH_NEITHER
    assert w["fake_breakout"] is False


def test_outcomes_event_bar_extremes_excluded():
    bars, R = plain_seq(pattern=PATTERN_C, extreme_event_bar=True)
    event = {"signal_time": T0 + 200 * STEP4, "direction": "long",
             "reference_price": 100.0}
    w = win(compute_outcomes(bars, event), 5)
    # 事件 bar high=entry+40*half（fav=40*half）不计 → MFE 只来自窗口；
    # 窗口 bar 经 round(...,6) 量化 → 相对误差 ~1e-7（与 oracle 测试同用 rel=1e-6）
    assert w["mfe_r"] == pytest.approx(0.4, rel=1e-6)
    assert w["mae_r"] == pytest.approx(0.5, rel=1e-6)
    # 事件 bar high=entry+40*half（fav=40*half）不计：若计入 mfe_r 会远大于 0.4
    assert w["mfe_r"] < 40.0 / R  # half=1.0：计入事件bar极值会 ≈ 40/R ≈ 5.28


def test_outcomes_windows_present():
    bars, _ = plain_seq(pattern=PATTERN_A, n_window=40)
    event = {"signal_time": T0 + 200 * STEP4, "direction": "long",
             "reference_price": 100.0}
    out = compute_outcomes(bars, event)
    assert [x["window"] for x in out["windows"]] == [5, 10, 20, 40]
    assert all(x["matured"] for x in out["windows"])


def test_outcomes_matured_flag_when_short():
    bars, _ = plain_seq(pattern=PATTERN_A, n_window=2)
    event = {"signal_time": T0 + 200 * STEP4, "direction": "long",
             "reference_price": 100.0}
    out = compute_outcomes(bars, event)
    w5 = win(out, 5)
    assert w5["matured"] is False
    assert w5["mfe_r"] is None
    assert win(out, 10)["matured"] is False
    assert win(out, 40)["matured"] is False


def test_outcomes_entry_is_signal_close():
    bars, _ = plain_seq(pattern=PATTERN_C)
    # 事件 bar close 改为 123.45
    bars[200] = dict(bars[200], open=123.45, high=124.45, low=122.45, close=123.45)
    event = {"signal_time": T0 + 200 * STEP4, "direction": "long"}  # 无 reference_price
    out = compute_outcomes(bars, event)
    assert out["entry"] == pytest.approx(123.45)


def test_outcomes_return_next_open_to_end_close():
    bars, R = plain_seq(pattern=PATTERN_A)
    event = {"signal_time": T0 + 200 * STEP4, "direction": "long",
             "reference_price": 100.0}
    w = win(compute_outcomes(bars, event), 5)
    # next_open = entry+0.1R（pattern bar1 o），end_close = entry-0.2R（bar5 c）
    assert w["next_open"] == pytest.approx(100.0 + 0.1 * R)
    assert w["end_close"] == pytest.approx(100.0 - 0.2 * R)
    assert w["return_r"] == pytest.approx(-0.3, rel=1e-9)  # (c-o)/R
    # return_pct 分母必须是 next_open（不是 signal close）
    assert w["return_pct"] == pytest.approx(-0.3 * R / w["next_open"], rel=1e-9)


def test_outcomes_short_return_direction():
    bars, R = plain_seq(pattern=PATTERN_A)
    event = {"signal_time": T0 + 200 * STEP4, "direction": "short",
             "reference_price": 100.0}
    w = win(compute_outcomes(bars, event), 5)
    # short：有利方向 = 下跌，(next_open - end_close)/R = +0.3R
    assert w["return_r"] == pytest.approx(0.3, rel=1e-9)
    assert w["return_pct"] == pytest.approx(0.3 * R / w["next_open"], rel=1e-9)


def test_outcomes_return_pct_denominator_is_next_open_gap():
    """gap 反例：下一开盘与 signal close 相差巨大时，return_pct 分母必须是 next_open，
    否则会严重失真；return_r 仍除事件 ATR14。"""
    bars, R = plain_seq(pattern=PATTERN_A)
    gap_open = 150.0  # 下一开盘跳空（≠ signal close=100）
    bars[201] = dict(bars[201], open=gap_open, high=gap_open + 0.5 * R,
                     low=100.0)  # low ≤ min(open, close) 保持 OHLC 合法
    event = {"signal_time": T0 + 200 * STEP4, "direction": "long",
             "reference_price": 100.0}
    w = win(compute_outcomes(bars, event), 5)
    end_close = w["end_close"]
    assert w["next_open"] == pytest.approx(gap_open)
    assert w["return_pct"] == pytest.approx(
        (end_close - gap_open) / gap_open, rel=1e-9)
    assert w["return_r"] == pytest.approx(
        (end_close - gap_open) / R, rel=1e-9)  # 分母仍是事件 ATR14


def test_outcomes_reference_price_mismatch_close_raises():
    # reference_price 与事件 bar close 相对偏差 0.5% >> 1e-6 → 拒绝
    bars, _ = plain_seq(pattern=PATTERN_C)
    event = {"signal_time": T0 + 200 * STEP4, "direction": "long",
             "reference_price": 100.5}
    with pytest.raises(ValueError):
        compute_outcomes(bars, event)


def test_outcomes_reference_price_nonpositive_raises():
    bars, _ = plain_seq(pattern=PATTERN_C)
    for bad in (0.0, -5.0):
        with pytest.raises(ValueError):
            compute_outcomes(bars, {"signal_time": T0 + 200 * STEP4,
                                    "direction": "long",
                                    "reference_price": bad})


def test_outcomes_reference_price_nonfinite_raises():
    bars, _ = plain_seq(pattern=PATTERN_C)
    for bad in (float("inf"), float("nan")):
        with pytest.raises(ValueError):
            compute_outcomes(bars, {"signal_time": T0 + 200 * STEP4,
                                    "direction": "long",
                                    "reference_price": bad})


def test_outcomes_reference_price_close_tolerance_ok():
    # 1e-6 相对容差内（量化误差）允许
    bars, _ = plain_seq(pattern=PATTERN_C)
    close = float(bars[200]["close"])
    event = {"signal_time": T0 + 200 * STEP4, "direction": "long",
             "reference_price": close * (1 + 5e-7)}
    out = compute_outcomes(bars, event)
    assert out["entry"] == pytest.approx(close * (1 + 5e-7), rel=1e-9)


def test_outcomes_buy_treated_as_long():
    bars, R = plain_seq(pattern=PATTERN_A)
    event = {"signal_time": T0 + 200 * STEP4, "direction": "buy",
             "reference_price": 100.0}
    out = compute_outcomes(bars, event)
    assert out["direction"] == "long"  # 归一
    w = win(out, 5)
    assert w["mfe_r"] == pytest.approx(1.2, rel=1e-9)
    assert w["first_touch"] == TOUCH_POSITIVE


def test_outcomes_sell_treated_as_short():
    bars, R = plain_seq(pattern=PATTERN_A)
    event = {"signal_time": T0 + 200 * STEP4, "direction": "sell",
             "reference_price": 100.0}
    out = compute_outcomes(bars, event)
    assert out["direction"] == "short"  # 归一
    w = win(out, 5)
    assert w["mfe_r"] == pytest.approx(0.5, rel=1e-9)
    assert w["first_touch"] == TOUCH_NEGATIVE


def test_outcomes_invalid_direction_raises():
    bars, _ = plain_seq(pattern=PATTERN_C)
    with pytest.raises(ValueError):
        compute_outcomes(bars, {"signal_time": T0 + 200 * STEP4,
                                "direction": "longish"})


def test_outcomes_unknown_event_bar_raises():
    bars, _ = plain_seq(pattern=PATTERN_C)
    with pytest.raises(ValueError):
        compute_outcomes(bars, {"signal_time": T0 + 500 * STEP4,
                                "direction": "long"})


def _build_oracle(direction: str, *, drift: float = 0.008,
                  pattern: list[tuple[float, float, float, float]] = PATTERN_A,
                  event_idx: int = 600) -> tuple[dict, list[dict], dict, float]:
    candles = make_candles(700, drift1w=drift, drift1d=drift)
    entry = float(candles["4h"][event_idx]["close"])
    candles["4h"][event_idx] = dict(
        candles["4h"][event_idx], close=entry,
        open=round(entry * 0.9, 6), high=round(entry * 1.5, 6),
        low=round(entry * 0.5, 6),
    )
    as_of = as_of_4h_close(event_idx)
    sig = T0 + event_idx * STEP4
    trig = {
        "strategy": "ma", "event_type": "ma_breakout", "direction": direction,
        "severity": "signal", "signal_time": sig, "reference_price": entry,
        "summary": "s",
        "payload": {
            "schema_version": "v1", "signal_type": "ma_breakout",
            "direction": direction, "state": "signal", "timeframe": "4h",
            "occurred_at": sig, "confirmed_at": sig, "reference_price": entry,
            "bar_closed": True, "evidence": {}, "parameters": {},
        },
    }
    d = evaluate("BTCUSDT", candles, as_of, trig, QUICK)
    assert d.eligible, d.reason_codes
    R = float(d.to_dict()["trigger"]["market_state"]["features"]["atr"])
    seq = list(candles["4h"][:event_idx + 1])
    o, h, l, c = pattern[0]
    seq += [{
        "time": T0 + (event_idx + 1 + k) * STEP4,
        "open": round(entry + o * R, 6), "high": round(entry + h * R, 6),
        "low": round(entry + l * R, 6), "close": round(entry + c * R, 6),
        "volume": 1000, "is_closed": True,
    } for k in range(40)]
    lab = label_decision(d, seq, as_of_4h_close(event_idx + 40), QUICK)
    event = {"signal_time": sig, "direction": direction,
             "reference_price": entry}
    return d, seq, lab, event


def _assert_oracle_match(lab: dict, out: dict) -> None:
    for w in (5, 10, 20, 40):
        lab_w = next(x for x in lab["windows"] if x["window"] == w)
        my_w = win(out, w)
        assert my_w["matured"] is True
        assert my_w["mfe_r"] == pytest.approx(lab_w["mfe_r"], rel=1e-6)
        assert my_w["mae_r"] == pytest.approx(lab_w["mae_r"], rel=1e-6)
        assert my_w["mfe_pct"] == pytest.approx(lab_w["mfe_pct"], rel=1e-6)
        assert my_w["mae_pct"] == pytest.approx(lab_w["mae_pct"], rel=1e-6)
        assert my_w["first_touch"] == lab_w["first_touch"]
        assert my_w["fake_breakout"] == lab_w["fake_breakout"]


def test_outcomes_oracle_compare_long():
    """短合成 A 事件：evaluate+label_decision 作 oracle 比对 mfe_r/mae_r/first/fake。"""
    _, seq, lab, event = _build_oracle("long")
    out = compute_outcomes(seq, event)
    assert out["direction"] == "long"
    assert out["atr_abs"] == pytest.approx(
        float(lab.to_dict()["risk"]["atr_abs"]), rel=1e-9)
    _assert_oracle_match(lab.to_dict(), out)


def test_outcomes_oracle_compare_short():
    _, seq, lab, event = _build_oracle("short", drift=-0.008)
    out = compute_outcomes(seq, event)
    assert out["direction"] == "short"
    _assert_oracle_match(lab.to_dict(), out)


# ═══════════════════════════════════════════════════════════════════════════════
#  7. wilson_interval / verify_manifest
# ═══════════════════════════════════════════════════════════════════════════════

def test_wilson_known_values():
    lo, hi = wilson_interval(50, 100, z=1.96)
    assert lo == pytest.approx(0.40382, abs=1e-4)
    assert hi == pytest.approx(0.59618, abs=1e-4)


def test_wilson_edges():
    lo0, hi0 = wilson_interval(0, 10, z=1.96)
    assert lo0 == 0.0
    assert hi0 == pytest.approx(0.27754, abs=1e-4)
    loN, hiN = wilson_interval(10, 10, z=1.96)
    assert hiN == 1.0
    assert loN == pytest.approx(0.72246, abs=1e-4)


def test_wilson_invalid_trials():
    with pytest.raises(ValueError):
        wilson_interval(0, 0)
    with pytest.raises(ValueError):
        wilson_interval(11, 10)


def _write_manifest(tmp_path: Path, manifest: dict, files: dict[str, dict],
                    corrupt: list[str] | None = None, corrupt_manifest: bool = False,
                    bad_sha: list[str] | None = None,
                    no_sha: list[str] | None = None):
    """写 manifest + 符号文件；自动为每个符号注入文件内容的 sha256 摘要。

    bad_sha：对指定 code 注入篡改摘要（0*64）；no_sha：不注入摘要。
    """
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    contents: dict[str, str] = {}
    for name, content in files.items():
        text = "not-json{{{" if name in (corrupt or []) else json.dumps(content)
        contents[name] = text
        (d / name).write_text(text, encoding="utf-8")
    syms = manifest.get("symbols")
    if isinstance(syms, list):
        for item in syms:
            if not isinstance(item, dict):
                continue
            code = item.get("code")
            if code and code not in (no_sha or []) and f"{code}.json" in contents:
                item["sha256"] = hashlib.sha256(
                    contents[f"{code}.json"].encode("utf-8")).hexdigest()
            if code and code in (bad_sha or []):
                item["sha256"] = "0" * 64  # 篡改摘要
    if corrupt_manifest:
        (tmp_path / "_manifest.json").write_text("not-json{{{", encoding="utf-8")
    else:
        (tmp_path / "_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path / "_manifest.json", d


def test_manifest_valid(tmp_path):
    manifest = {"generated_at": "2026-01-01T00:00:00",
                "symbols": [{"code": "BTC-USD", "name": "BTC"},
                            {"code": "XAUUSD", "name": "Gold"}]}
    files = {
        "BTC-USD.json": {"symbol": "BTC-USD", "name": "BTC", "bar_count": 1200},
        "XAUUSD.json": {"symbol": "XAUUSD", "name": "Gold", "bar_count": 900},
    }
    mp, dd = _write_manifest(tmp_path, manifest, files)
    r = verify_manifest(mp, dd)
    assert r["valid"] is True
    assert r["errors"] == []


def test_manifest_corrupt_json(tmp_path):
    manifest = {"symbols": [{"code": "BTC-USD", "name": "BTC"}]}
    files = {"BTC-USD.json": {"symbol": "BTC-USD", "name": "BTC"}}
    mp, dd = _write_manifest(tmp_path, manifest, files, corrupt_manifest=True)
    r = verify_manifest(mp, dd)
    assert r["valid"] is False
    assert any("JSON" in e for e in r["errors"])


def test_manifest_missing_symbols_key(tmp_path):
    manifest = {"generated_at": "2026-01-01T00:00:00"}
    mp, dd = _write_manifest(tmp_path, manifest, {})
    r = verify_manifest(mp, dd)
    assert r["valid"] is False
    assert any("symbols" in e for e in r["errors"])


def test_manifest_missing_symbol_file(tmp_path):
    manifest = {"symbols": [{"code": "BTC-USD", "name": "BTC"}]}
    mp, dd = _write_manifest(tmp_path, manifest, {})
    r = verify_manifest(mp, dd)
    assert r["valid"] is False
    assert any("BTC-USD.json" in e for e in r["errors"])


def test_manifest_symbol_field_mismatch(tmp_path):
    manifest = {"symbols": [{"code": "BTC-USD", "name": "BTC"}]}
    files = {"BTC-USD.json": {"symbol": "OTHER", "name": "BTC"}}
    mp, dd = _write_manifest(tmp_path, manifest, files)
    r = verify_manifest(mp, dd)
    assert r["valid"] is False
    assert any("symbol" in e for e in r["errors"])


def test_manifest_symbol_file_corrupt(tmp_path):
    manifest = {"symbols": [{"code": "BTC-USD", "name": "BTC"}]}
    files = {"BTC-USD.json": {"symbol": "BTC-USD", "name": "BTC"}}
    mp, dd = _write_manifest(tmp_path, manifest, files, corrupt=["BTC-USD.json"])
    r = verify_manifest(mp, dd)
    assert r["valid"] is False
    assert any("BTC-USD.json" in e and "JSON" in e for e in r["errors"])


def test_manifest_bad_symbol_item(tmp_path):
    manifest = {"symbols": [{"code": "", "name": "Empty"}, "not-a-dict"]}
    mp, dd = _write_manifest(tmp_path, manifest, {})
    r = verify_manifest(mp, dd)
    assert r["valid"] is False
    assert r["errors"]


def test_manifest_sha256_valid(tmp_path):
    manifest = {"symbols": [{"code": "BTC-USD", "name": "BTC"}]}
    files = {"BTC-USD.json": {"symbol": "BTC-USD", "name": "BTC", "bar_count": 10}}
    mp, dd = _write_manifest(tmp_path, manifest, files)
    r = verify_manifest(mp, dd)
    assert r["valid"] is True
    assert r["errors"] == []


def test_manifest_sha256_mismatch(tmp_path):
    # 文件内容被篡改但 manifest 摘要未更新 → sha256 不一致
    manifest = {"symbols": [{"code": "BTC-USD", "name": "BTC"}]}
    files = {"BTC-USD.json": {"symbol": "BTC-USD", "name": "BTC"}}
    mp, dd = _write_manifest(tmp_path, manifest, files, bad_sha=["BTC-USD"])
    r = verify_manifest(mp, dd)
    assert r["valid"] is False
    assert any("sha256" in e for e in r["errors"])


def test_manifest_sha256_missing(tmp_path):
    # 符号项缺 sha256 摘要 → 校验失败
    manifest = {"symbols": [{"code": "BTC-USD", "name": "BTC"}]}
    files = {"BTC-USD.json": {"symbol": "BTC-USD", "name": "BTC"}}
    mp, dd = _write_manifest(tmp_path, manifest, files, no_sha=["BTC-USD"])
    r = verify_manifest(mp, dd)
    assert r["valid"] is False
    assert any("sha256" in e for e in r["errors"])


# ═══════════════════════════════════════════════════════════════════════════════
#  8. MTF-BTC-V1 流水线 helper（冻结/合并/对齐/汇总/确定性）
# ═══════════════════════════════════════════════════════════════════════════════

def _zip_bytes(csv_text: str, name: str = "data.csv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, csv_text)
    return buf.getvalue()


class _FakeResp:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self) -> bytes:
        return self._body


# ── 8.1 gzip 确定性 + sha 篡改 ────────────────────────────────────────────────

def test_gzip_json_deterministic_mtime0(tmp_path: Path):
    p = tmp_path / "c.json.gz"
    payload = {"b": [1.5, 2.0], "a": {"z": 1, "y": None}}
    sha1 = write_gzip_json_deterministic(p, payload)
    raw1 = p.read_bytes()
    sha2 = write_gzip_json_deterministic(p, payload)
    assert raw1 == p.read_bytes()  # 两次写入字节一致
    assert sha1 == sha2
    with gzip.GzipFile(fileobj=io.BytesIO(raw1)) as gz:
        gz.read()  # 触发解压头解析
        assert gz.mtime == 0  # 确定性 gzip 头
    assert read_gzip_json(p) == {"a": {"y": None, "z": 1}, "b": [1.5, 2.0]}


def test_gzip_json_sha_tamper_detected(tmp_path: Path):
    p = tmp_path / "c.json.gz"
    sha = write_gzip_json_deterministic(p, {"bars": [1, 2, 3]})
    raw = bytearray(p.read_bytes())
    raw[len(raw) // 2] ^= 0xFF  # 篡改压缩字节
    tampered = tmp_path / "tampered.gz"
    tampered.write_bytes(bytes(raw))
    assert hashlib.sha256(bytes(raw)).hexdigest() != sha  # 防篡改摘要不一致


# ── 8.2 HTTP 非 200 不读 zip ──────────────────────────────────────────────────

def test_fetch_http_404_not_read_zip(monkeypatch):
    seen: list[str] = []

    def fake_urlopen(req, timeout=None, context=None):
        seen.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    r = fetch_binance_zip("https://x/not-here.zip")
    assert r["ok"] is False
    assert r["http_status"] == 404
    assert "rows" not in r          # 非 200 绝不读作 zip
    assert "content_sha256" not in r
    assert seen == ["https://x/not-here.zip"]


def test_fetch_http_500_recorded_not_read_zip(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda req, timeout=None, context=None: _FakeResp(500, b"boom"),
    )
    r = fetch_binance_zip("https://x/err.zip")
    assert r["ok"] is False and r["http_status"] == 500
    assert "rows" not in r


def test_fetch_200_bad_zip_records_error(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda req, timeout=None, context=None: _FakeResp(200, b"not-a-zip"),
    )
    r = fetch_binance_zip("https://x/bad.zip")
    assert r["ok"] is False
    assert "zip" in r["error"].lower() or "损坏" in r["error"]


def test_fetch_200_parses_zip_and_content_sha(monkeypatch):
    body = _zip_bytes(
        "1704067200000,100,101,99,100.5,1234\n"
        "1704068640000,100.5,102,100,101.5,888\n"
    )
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda req, timeout=None, context=None: _FakeResp(200, body),
    )
    r = fetch_binance_zip("https://x/ok.zip")
    assert r["ok"] is True
    assert r["row_count"] == 2
    assert r["rows"][0]["time"] == 1_704_067_200  # 毫秒 → 秒
    assert r["rows"][1]["close"] == 101.5
    assert r["content_sha256"] == hashlib.sha256(body).hexdigest()


def test_parse_zip_csv_no_header_kept():
    body = _zip_bytes("1578268800000,7354.36,8468.42,7345.00,8186.70,1484603.363\n")
    rows = parse_zip_csv(body)
    assert len(rows) == 1  # 无表头首根不丢
    assert rows[0]["time"] == 1_578_268_800


# ── 8.3 合并去重 / 内容冲突 / 连续性 ─────────────────────────────────────────

def test_merge_4h_same_content_dedupes():
    bars = make_bars(10, STEP4)
    merged = merge_4h_batches([bars, bars])
    assert len(merged) == 10  # 相同内容幂等去重
    assert [b["time"] for b in merged] == [b["time"] for b in bars]


def test_merge_4h_conflict_rejects():
    bars = make_bars(10, STEP4)
    bad = [dict(b, close=float(b["close"]) + 1.0) for b in bars]
    with pytest.raises(ValueError, match="内容冲突"):
        merge_4h_batches([bars, bad])


def test_merge_4h_unsorted_input_sorted():
    bars = make_bars(10, STEP4)
    merged = merge_4h_batches([bars[5:], bars[:5]])  # 乱序输入
    assert [b["time"] for b in merged] == [b["time"] for b in bars]


def test_merge_4h_gap_rejected():
    bars = make_bars(10, STEP4)
    gap = [b for b in bars if b["time"] != bars[3]["time"]]  # 中间缺一根
    with pytest.raises(ValueError, match="不连续"):
        merge_4h_batches([gap])


# ── 8.4 bisect as-of 无未来 ─────────────────────────────────────────────────

def test_bisect_closed_index_no_future():
    close_times = [10, 20, 30]
    assert bisect_closed_index(close_times, 30) == 2
    assert bisect_closed_index(close_times, 29) == 1   # 未到期 bar 不可见
    assert bisect_closed_index(close_times, 10) == 0
    assert bisect_closed_index(close_times, 9) == -1   # 无已收盘 bar


def test_closed_state_at_alignment_no_future():
    bars = make_bars(100, STEP4)
    states = precompute_market_states(bars, QUICK)
    # as_of = 第 5 根 close → 最新状态恰为第 5 根（不可能是更晚的 bar）
    st, reason = closed_state_at(states, bars, STEP4, bars[4]["time"] + STEP4)
    assert st is not None
    assert st.bar_time == bars[4]["time"]  # as_of=第 5 根 close → 恰为第 5 根状态
    assert reason  # QUICK 历史不足 → 未 ready 但状态正确（明示原因）
    # as_of 落在第 5、6 根之间 → 最新已收盘是第 5 根
    st2, _ = closed_state_at(states, bars, STEP4, bars[5]["time"] + STEP4 - 1)
    assert st2.bar_time == bars[4]["time"]
    # as_of 早于第一根 close → 找不到并明示原因
    st3, reason3 = closed_state_at(states, bars, STEP4, bars[0]["time"] + STEP4 - 1)
    assert st3 is None and reason3


def test_closed_state_at_reports_not_ready():
    bars = make_bars(5, STEP4)  # 历史过短 → 未 ready
    states = precompute_market_states(bars, QUICK)
    st, reason = closed_state_at(states, bars, STEP4, bars[-1]["time"] + STEP4)
    assert st is not None and st.ready is False
    assert reason  # 明示原因


# ── 8.5 summary 只从 CSV 复算 ────────────────────────────────────────────────

def _mkrow(time: int, direction: str, window: int, *,
           matured: bool = True, ret_r: float = 0.5, ret_pct: float = 0.01,
           mfe_r: float = 1.2, mae_r: float = 0.4,
           first: str = "positive_first", fake: bool | None = False,
           A: int = 1, B: int = 1, wr: str = "agree",
           et: str = "ma_breakout", strat: str = "ma",
           svp: str = "expansion", tvp: str = "expansion",
           tcs: str = "strong", tvs: str = "high", year: int = 2024) -> dict:
    return {
        "event_key": f"{strat}|{et}|{direction}|{time}",
        "time": time, "year": year, "raw_direction": direction,
        "direction": direction, "event_type": et, "strategy": strat,
        "reference": 100.0, "A": A, "B": B, "B_minus_A": int(B and not A),
        "week_relation": wr, "classify_reason": "", "week_trend": "up",
        "day_trend": "up", "trigger_trend": "up", "setup_vol_phase": svp,
        "trigger_vol_phase": tvp, "trigger_candle_strength": tcs,
        "trigger_volume_state": tvs, "window": window,
        "matured": 1 if matured else 0, "mfe_r": mfe_r, "mfe_pct": 0.02,
        "mae_r": mae_r, "mae_pct": 0.01, "first_touch": first,
        "fake_breakout": fake, "next_open": 100.0, "return_r": ret_r,
        "return_pct": ret_pct,
    }


def _mkrows_35() -> list[dict]:
    """35 个事件（time 互异）：30 long / 5 short；5 个 B_minus_A；
    全部 window 5/10/20/40 已成熟。"""
    rows: list[dict] = []
    for i in range(35):
        t = 1_000_000 + i * 100
        direction = "short" if i >= 30 else "long"
        wr = "disagree"
        A, B = 0, 1
        if i < 25:
            wr, A, B = "agree", 1, 1
        elif i < 30:
            wr, A, B = "sideways", 0, 1  # B_minus_A（sideways）
        # B_minus_A（disagree）：i in 30..34
        ret_r = 0.6 if i % 2 == 0 else -0.3
        fake = (i % 3 == 0)
        first = "negative_first" if fake else (
            "positive_first" if ret_r > 0 else "neither")
        svp = "contraction" if i % 2 == 0 else "expansion"
        tvp = "transition" if i % 3 == 0 else "expansion"
        tcs = "strong" if i % 2 == 0 else "weak"
        for w in (5, 10, 20, 40):
            rows.append(_mkrow(
                t, direction, w, ret_r=ret_r,
                ret_pct=ret_r * 0.001, mfe_r=1.2 + (i % 3) * 0.1,
                mae_r=0.4 + (i % 3) * 0.1, first=first, fake=fake,
                A=A, B=B, wr=wr, svp=svp, tvp=tvp, tcs=tcs, year=2023 + (i % 3),
            ))
    return rows


def test_summary_recomputed_from_csv(tmp_path):
    csv_path = tmp_path / "events.csv"
    write_events_csv(sort_event_rows(_mkrows_35()), csv_path)
    s = summarize_events_csv(csv_path)

    overall5 = s["cohorts"]["raw"]["overall"]["5"]
    assert overall5["n"] == 35
    assert overall5["unique_entry_points"] == 35
    assert overall5["exploratory"] is False            # n>=30
    win_count = sum(1 for i in range(35) if (0.6 if i % 2 == 0 else -0.3) > 0)
    assert overall5["next_open_win"]["count"] == win_count
    assert overall5["next_open_win"]["wilson95"] == \
        [round(x, 6) for x in wilson_interval(win_count, 35)]
    assert overall5["first"]["positive_first"] + overall5["first"]["negative_first"] \
        + overall5["first"]["neither"] + overall5["first"]["ambiguous_same_bar"] == 35
    known = 35 - sum(1 for i in range(35) if i % 3 == 0 and False)  # 无 ambiguous
    fake_count = sum(1 for i in range(35) if i % 3 == 0)
    assert overall5["fake"]["known"] == known
    assert overall5["fake"]["count"] == fake_count
    assert overall5["fake"]["rate"] == round(fake_count / known, 6)
    assert overall5["fake"]["wilson95"] == \
        [round(x, 6) for x in wilson_interval(fake_count, known)]

    # 小样本 cohort → exploratory
    bma = s["cohorts"]["B_minus_A"]["overall"]["5"]
    assert bma["n"] == 10          # 5 short(disagree) + 5 sideways = 10
    assert bma["exploratory"] is True

    # 分组维度存在且与 overall 汇总一致（by_direction 各方向 n 之和 == overall n）
    assert s["cohorts"]["raw"]["by_direction"]["long"]["5"]["n"] + \
        s["cohorts"]["raw"]["by_direction"]["short"]["5"]["n"] == \
        s["cohorts"]["raw"]["overall"]["5"]["n"]
    assert s["cohorts"]["raw"]["by_year"]  # 2023/2024/2025
    assert set(s["cohorts"]["raw"]["by_trigger_vol_phase"].keys()) == {"expansion", "transition"}
    assert set(s["cohorts"]["raw"]["by_trigger_candle_strength"].keys()) == {"strong", "weak"}

    # decision_level 去重：35 事件 time 互异 → 每 (time,direction,window)
    # 一行，与 raw 完全一致
    assert s["decision_level"]["overall"]["5"]["n"] == 35
    assert s["decision_rows"] == 35 * 4
    assert s["events_rows"] == 35 * 4


def test_summary_unmatured_excluded(tmp_path):
    rows = []
    for i in range(3):
        for w in (5, 10, 20, 40):
            r = _mkrow(2000 + i, "long", w,
                       matured=(w <= 10), ret_r=0.5)
            rows.append(r)
    csv_path = tmp_path / "events.csv"
    write_events_csv(sort_event_rows(rows), csv_path)
    s = summarize_events_csv(csv_path)
    assert s["cohorts"]["raw"]["overall"]["5"]["n"] == 3
    assert s["cohorts"]["raw"]["overall"]["20"]["n"] == 0   # 未成熟不计
    assert s["cohorts"]["raw"]["overall"]["20"]["exploratory"] is True
    assert s["cohorts"]["raw"]["overall"]["20"]["median_return_pct"] is None


def test_summary_decisions_dedup_same_root(tmp_path):
    """同一根上 2 个策略信号 → events_rows 翻倍；decision 每窗口留 1。"""
    rows = []
    for strat, et in (("ma", "ma_breakout"), ("structure", "trendline_breakout")):
        for w in (5, 10, 20, 40):
            rows.append(_mkrow(7777, "long", w, strat=strat, et=et))
    csv_path = tmp_path / "events.csv"
    write_events_csv(sort_event_rows(rows), csv_path)
    s = summarize_events_csv(csv_path)
    assert s["events_rows"] == 8
    assert s["decision_rows"] == 4
    assert s["decision_level"]["overall"]["5"]["n"] == 1
    assert s["decision_level"]["overall"]["5"]["unique_entry_points"] == 1


def test_dedupe_decisions_picks_stable_first():
    rows = []
    for strat, et in (("structure", "trendline_breakout"), ("ma", "ma_breakout"),
                      ("rsi", "rsi_divergence")):
        for w in (5, 10):
            rows.append(_mkrow(7777, "long", w, strat=strat, et=et))
    dec = dedupe_decisions(rows)
    assert len(dec) == 2  # 2 窗口 × (time,direction,window) 每窗口留 1
    assert {r["strategy"] for r in dec} == {"ma"}  # 稳定取排序后最小
    ns = decision_n_signals(rows)
    assert ns[(7777, "long")] == 3  # 同 (time,direction) 唯一 event_key 数（非 6）


def test_summary_cohort_membership_invariants(tmp_path):
    """cohort 非互斥成员关系回归（每个 window）：
    raw=全部行；B = A + B_minus_A；B_week agree/disagree/sideways 之和 == B。"""
    rows = _mkrows_35()  # 25 agree(A+B) / 5 sideways(B_minus_A) / 5 disagree(B_minus_A)
    csv_path = tmp_path / "events.csv"
    write_events_csv(sort_event_rows(rows), csv_path)
    s = summarize_events_csv(csv_path)
    for w in (5, 10, 20, 40):
        ws = str(w)
        cohorts = s["cohorts"]
        raw_n = cohorts["raw"]["overall"][ws]["n"]
        a_n = cohorts["A"]["overall"][ws]["n"]
        b_n = cohorts["B"]["overall"][ws]["n"]
        bma_n = cohorts["B_minus_A"]["overall"][ws]["n"]
        agree = cohorts["B_week_agree"]["overall"][ws]["n"]
        disagree = cohorts["B_week_disagree"]["overall"][ws]["n"]
        sideways = cohorts["B_week_sideways"]["overall"][ws]["n"]
        assert raw_n == 35                       # raw 恒含全部行
        assert b_n == a_n + bma_n                # B = A + B_minus_A（A 行必须在 B 内）
        assert agree + disagree + sideways == b_n  # 三周组之和 == B
        assert a_n == 25 and bma_n == 10
        assert agree == 25 and disagree == 5 and sideways == 5


def test_summary_unique_entry_points_by_time_direction(tmp_path):
    """同一时刻同时有 long/short → unique_entry_points 按 (time,direction) 计 2；
    只按 time 去重会误报 1。"""
    rows = []
    for direction in ("long", "short"):
        for w in (5, 10, 20, 40):
            rows.append(_mkrow(5555, direction, w, A=1, B=1))
    csv_path = tmp_path / "events.csv"
    write_events_csv(sort_event_rows(rows), csv_path)
    s = summarize_events_csv(csv_path)
    raw5 = s["cohorts"]["raw"]["overall"]["5"]
    assert raw5["n"] == 2
    assert raw5["unique_entry_points"] == 2   # 反例：同 time long/short 是两个 entry point
    assert s["decision_level"]["overall"]["5"]["unique_entry_points"] == 2  # 同口径


def test_dedupe_decisions_long_short_same_time_kept():
    """同一时刻的 long/short 是两个 decision：去重键是 (time,direction,window)，
    不得把不同方向合并。"""
    rows = []
    for direction in ("long", "short"):
        for w in (5, 10, 20, 40):
            rows.append(_mkrow(9999, direction, w))
    dec = dedupe_decisions(rows)
    assert len(dec) == 8  # 2 direction × 4 window
    assert {(r["time"], r["direction"]) for r in dec} == \
        {(9999, "long"), (9999, "short")}
    ns = decision_n_signals(rows)
    assert ns[(9999, "long")] == 1
    assert ns[(9999, "short")] == 1


def test_n_signals_ignores_long_format_window_duplicates():
    """同一事件在 long-format 的 4 个 window 行不重复计 n_signals；
    多策略同根按唯一 event_key 计数。"""
    rows = []
    for strat, et in (("ma", "ma_x"), ("structure", "structure_x"),
                      ("rsi", "rsi_x")):
        for w in (5, 10, 20, 40):
            rows.append(_mkrow(8888, "long", w, strat=strat, et=et))
    ns = decision_n_signals(rows)
    assert ns[(8888, "long")] == 3  # 3 策略 × 1 事件（忽略 window 重复，非 12）
    dec = dedupe_decisions(rows)
    assert len(dec) == 4  # 每 window 留 1


# ── 8.6 同冻结输入 run 两次 → events.csv / summary.json 字节一致 ──────────────

def test_run_twice_byte_identical(tmp_path):
    bars = make_flat_with_breakout(600, breakouts=[420, 500])
    d1, d2 = tmp_path / "r1", tmp_path / "r2"
    out1 = run_backtest_pipeline(
        bars, ["ma", "structure", "macd"], {},
        d1 / "events.csv", d1 / "summary.json", d1 / "decisions.csv",
        config=QUICK,
    )
    out2 = run_backtest_pipeline(
        bars, ["ma", "structure", "macd"], {},
        d2 / "events.csv", d2 / "summary.json", d2 / "decisions.csv",
        config=QUICK,
    )
    assert (d1 / "events.csv").read_bytes() == (d2 / "events.csv").read_bytes()
    assert (d1 / "summary.json").read_bytes() == (d2 / "summary.json").read_bytes()
    assert (d1 / "decisions.csv").read_bytes() == (d2 / "decisions.csv").read_bytes()
    assert out1["events_sha256"] == out2["events_sha256"]
    assert out1["summary_sha256"] == out2["summary_sha256"]
    assert out1["decisions_sha256"] == out2["decisions_sha256"]
    assert out1["event_count"] == out2["event_count"] >= 1
    # summary 确系从 events.csv 复算：删除 CSV 后无法复算
    (d1 / "events.csv").unlink()
    with pytest.raises(FileNotFoundError):
        summarize_events_csv(d1 / "events.csv")


def test_run_pipeline_unclosed_tail_deterministic(tmp_path):
    """未收盘尾巴（bar 无 OHLC）也须确定：事件结果与无尾巴时一致。"""
    bars = make_flat_with_breakout(500, breakouts=[400])
    tail = [{"time": bars[-1]["time"] + STEP4, "is_closed": False}] * 5
    d1, d2 = tmp_path / "a", tmp_path / "b"
    run_backtest_pipeline(bars, ["ma"], {}, d1 / "e.csv", d1 / "s.json",
                          d1 / "d.csv", config=QUICK)
    run_backtest_pipeline(bars + tail, ["ma"], {}, d2 / "e.csv", d2 / "s.json",
                          d2 / "d.csv", config=QUICK)
    # 比较实际写入的文件名（e.csv / s.json / d.csv）
    assert (d1 / "e.csv").read_bytes() == (d2 / "e.csv").read_bytes()
    assert (d1 / "s.json").read_bytes() == (d2 / "s.json").read_bytes()
    assert (d1 / "d.csv").read_bytes() == (d2 / "d.csv").read_bytes()


# ── 9. MTF-BTC-V1 CLI fetch/verify（本地 fake HTTP 字节；不 mock 核心函数）──────
#   fetch/verify 走 app.core.mtf_historical_backtest 的 http_get /
#   fetch_binance_zip / parse_binance_kline_csv / validate_closed_4h /
#   aggregate_utc；网络层用本地 fake HTTP 字节（monkeypatch
#   urllib.request.urlopen），不 mock 任何核心函数。

import datetime

from app.tasks.mtf_backtest_cli import (
    DERIVED_1DAY,
    DERIVED_1WEEK,
    EXPECTED_FIRST_TIME,
    EXPECTED_LAST_TIME,
    FETCH_METADATA,
    RAW_4H,
    RUN_DECISIONS,
    RUN_EVENTS,
    RUN_MANIFEST,
    RUN_SHA,
    RUN_SUMMARY,
    SHA_MANIFEST,
    SOURCE_MANIFEST,
    all_urls,
    collect_sources,
    daily_url,
    fetch,
    monthly_url,
    run,
    verify,
)

# 小型相邻样本：2026-07 月文件 + 2026-08-01/02 日文件（共 198 根，全程 14400 连续）。
# 生产 fetch/collect_sources 默认仍走 all_urls() 全量范围（见
# test_cli_all_urls_full_range / test_collect_sources_default_uses_all_urls）；
# 测试只传 3 个相邻 URL，避免每个测试生成/压缩 2020-2026 约 1.5 万根。
CLI_URLS = [
    monthly_url(2026, 7),
    daily_url(2026, 8, 1),
    daily_url(2026, 8, 2),
]
CLI_T0 = int(datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc).timestamp())  # 2026-07-01T00:00:00Z
CLI_LAST = int(datetime.datetime(2026, 8, 2, 20, 0, tzinfo=datetime.timezone.utc).timestamp())  # 2026-08-02T20:00:00Z 最后一根 4H 开盘
CLI_N = (CLI_LAST - CLI_T0) // STEP4 + 1  # 198


def test_cli_all_urls_full_range():
    """URL 生成范围独立测试：完整 all_urls() 的数量与首末（不生成/压缩数据）。"""
    urls = all_urls()
    months = (2026 - 2020) * 12 + 7          # 2020-01 .. 2026-07 = 79 个月
    days = 14                                 # 2026-08-01 .. 14 每日文件
    assert len(urls) == months + days
    assert urls[0] == monthly_url(2020, 1)
    assert urls[-1] == daily_url(2026, 8, 14)
    assert urls[-2] == daily_url(2026, 8, 13)
    assert monthly_url(2026, 7) in urls       # 月/日接缝两侧都在
    assert daily_url(2026, 8, 1) in urls


def test_collect_sources_default_uses_all_urls(monkeypatch):
    """生产默认（urls=None）必须仍拉 all_urls() 全量范围；
    用 404 假响应避免任何真实网络/数据开销。"""
    seen: list[str] = []

    def fake_fetch(url, timeout=60.0):
        seen.append(url)
        return {"url": url, "http_status": 404, "ok": False,
                "error": "HTTP 404"}

    monkeypatch.setattr(
        "app.tasks.mtf_backtest_cli.fetch_binance_zip", fake_fetch)
    records, batches = collect_sources()
    assert [r["url"] for r in records] == all_urls()
    assert seen == all_urls()
    assert batches == []  # 非 200 不进批次


def _cli_close(i: int) -> float:
    return 1000.0 + i * 0.5


def _cli_csv(t0: int, ts: list[int], *, header: bool = False,
             close_delta: float = 0.0) -> str:
    lines: list[str] = []
    if header:
        lines.append(
            "open_time,open,high,low,close,volume,"
            "close_time,quote_volume,count,taker_buy_volume,"
            "taker_buy_quote_volume,ignore"
        )
    for t in ts:
        i = (t - t0) // STEP4
        close = _cli_close(i) + close_delta
        op = close - 4.0
        hi = close + 6.0
        lo = op - 6.0
        vol = 50.0 + (i % 97)
        lines.append(f"{t * 1000},{op:.4f},{hi:.4f},{lo:.4f},{close:.4f},{vol:.4f}")
    return "\n".join(lines) + "\n"


def _cli_day_ts(year: int, month: int, day: int) -> list[int]:
    start = int(datetime.datetime(year, month, day,
                                  tzinfo=datetime.timezone.utc).timestamp())
    return [start + i * STEP4 for i in range(6)]


def _cli_bodies(*, header_urls: set[str] | None = None,
                overrides: dict[str, tuple[int, bytes]] | None = None
                ) -> dict[str, tuple[int, bytes]]:
    """按 CLI_URLS（3 个相邻 URL / 198 根连续 4H）生成本地 fake HTTP 字节。"""
    bodies: dict[str, tuple[int, bytes]] = {}
    by_url: dict[str, list[int]] = {u: [] for u in CLI_URLS}
    for t in range(CLI_T0, CLI_LAST + 1, STEP4):
        dt = datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc)
        if (dt.year, dt.month) == (2026, 7):
            url = monthly_url(dt.year, dt.month)
        else:
            url = daily_url(dt.year, dt.month, dt.day)
        by_url[url].append(t)
    headers = header_urls or set()
    for url, ts in by_url.items():
        if overrides and url in overrides:
            bodies[url] = overrides[url]
            continue
        bodies[url] = (200, _zip_bytes(_cli_csv(CLI_T0, ts, header=url in headers)))
    return bodies


class _CliFakeHTTP:
    def __init__(self, bodies: dict[str, tuple[int, bytes]]):
        self.bodies = bodies
        self.calls: list[str] = []

    def __call__(self, req, timeout=None, context=None):
        self.calls.append(req.full_url)
        status, body = self.bodies[req.full_url]
        return _FakeResp(status, body)


def _use_fake_http(monkeypatch, bodies: dict[str, tuple[int, bytes]]) -> _CliFakeHTTP:
    fake = _CliFakeHTTP(bodies)
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return fake


def _cli_manifest(out: Path) -> dict:
    return json.loads((out / SOURCE_MANIFEST).read_text(encoding="utf-8"))


def _cli_fetch(out, **kwargs):
    """fetch 的 CLI 小样本封装：固定 CLI_URLS 与期望首末边界（缺头/缺尾也拒绝）。"""
    return fetch(out, urls=CLI_URLS, expected_first_time=CLI_T0,
                 expected_last_time=CLI_LAST, **kwargs)


# run_manifest 的 A/B 策略规则串必须与实际 classify_policies 实现一致
# （含 trigger.is_closed；A 严格多一个 week.trend == event_direction 约束）。
EXPECTED_POLICY_A_RULE = (
    "week.ready AND week.is_closed AND day.ready AND day.is_closed AND "
    "trigger.ready AND trigger.is_closed AND "
    "day.trend == event_direction AND week.trend == event_direction"
)
EXPECTED_POLICY_B_RULE = (
    "week.ready AND week.is_closed AND day.ready AND day.is_closed AND "
    "trigger.ready AND trigger.is_closed AND "
    "day.trend == event_direction"
)


# ── 9.1 HTTP 非 200：只记录、绝不交 zip 解析 ─────────────────────────────────

def test_cli_fetch_404_recorded_never_read_zip(monkeypatch, tmp_path):
    bad_url = daily_url(2026, 8, 1)
    fake = _use_fake_http(monkeypatch, _cli_bodies(
        overrides={bad_url: (404, b"<html>404 not found, not a zip</html>")},
    ))
    records, batches = collect_sources(CLI_URLS)
    bad = next(r for r in records if r["url"] == bad_url)
    assert bad["http_status"] == 404 and bad["ok"] is False
    assert bad["error"] == "HTTP 404"          # 绝不出现 "zip 损坏"
    assert "content_sha256" not in bad         # 404 无内容摘要
    assert bad_url in fake.calls
    assert len(batches) == len(CLI_URLS) - 1   # 非 200 不进批次
    # 完整 fetch：中间日文件缺失 → 连续性缺口 → ValueError（绝非 zip 解析错误）
    with pytest.raises(ValueError, match="不连续"):
        _cli_fetch(tmp_path)


# ── 9.2 连续性缺口 ───────────────────────────────────────────────────────────

def test_cli_fetch_gap_rejected(monkeypatch, tmp_path):
    d01 = _cli_day_ts(2026, 8, 1)
    gap_body = _zip_bytes(_cli_csv(CLI_T0, d01[:1] + d01[2:]))  # 丢掉 04:00 那根
    _use_fake_http(monkeypatch, _cli_bodies(
        overrides={daily_url(2026, 8, 1): (200, gap_body)},
    ))
    with pytest.raises(ValueError, match="不连续"):
        _cli_fetch(tmp_path)


def test_cli_fetch_missing_head_rejected(monkeypatch, tmp_path):
    """首 URL（2026-07 月文件）内容去掉 CLI_T0 → 合并后首根 ≠ 期望 → 缺头
    （剩余数据内部仍严格 14400 连续，绝不得误报“不连续”）。"""
    all_ts = [CLI_T0 + i * STEP4 for i in range(CLI_N)]
    july_ts = [t for t in all_ts
               if datetime.datetime.fromtimestamp(
                   t, tz=datetime.timezone.utc).month == 7]
    head_less = _zip_bytes(_cli_csv(CLI_T0, july_ts[1:]))  # 去掉 2026-07-01T00:00
    _use_fake_http(monkeypatch, _cli_bodies(
        overrides={CLI_URLS[0]: (200, head_less)},
    ))
    with pytest.raises(ValueError, match="缺头"):
        _cli_fetch(tmp_path)


def test_cli_fetch_missing_tail_rejected(monkeypatch, tmp_path):
    """末 URL（2026-08-02 日文件）内容去掉 CLI_LAST → 合并后末根 ≠ 期望 → 缺尾
    （剩余数据内部仍严格 14400 连续，绝不得误报“不连续”）。"""
    all_ts = [CLI_T0 + i * STEP4 for i in range(CLI_N)]
    aug2_ts = [t for t in all_ts
               if datetime.datetime.fromtimestamp(
                   t, tz=datetime.timezone.utc).day == 2]
    tail_less = _zip_bytes(_cli_csv(CLI_T0, aug2_ts[:-1]))  # 去掉 2026-08-02T20:00
    _use_fake_http(monkeypatch, _cli_bodies(
        overrides={CLI_URLS[-1]: (200, tail_less)},
    ))
    with pytest.raises(ValueError, match="缺尾"):
        _cli_fetch(tmp_path)


# ── 9.3 重复相同去重 / 内容冲突拒绝 ─────────────────────────────────────────

def test_cli_fetch_duplicate_identical_deduped(monkeypatch, tmp_path):
    d01 = _cli_day_ts(2026, 8, 1)
    d02 = _cli_day_ts(2026, 8, 2)
    body = _zip_bytes(_cli_csv(CLI_T0, d01 + d02))  # 08-01 文件多塞 08-02 全部根
    _use_fake_http(monkeypatch, _cli_bodies(overrides={
        daily_url(2026, 8, 1): (200, body),
        daily_url(2026, 8, 2): (200, body),        # 与 08-01 中 08-02 根逐字相同
    }))
    res = _cli_fetch(tmp_path)
    assert res["bar_count"] == CLI_N  # 相同内容幂等去重，不重复计数
    raw = read_gzip_json(tmp_path / RAW_4H)
    assert [b["time"] for b in raw] == [CLI_T0 + i * STEP4 for i in range(CLI_N)]


def test_cli_fetch_conflict_rejected(monkeypatch, tmp_path):
    d01 = _cli_day_ts(2026, 8, 1)
    d02 = _cli_day_ts(2026, 8, 2)
    _use_fake_http(monkeypatch, _cli_bodies(overrides={
        daily_url(2026, 8, 1): (200, _zip_bytes(_cli_csv(CLI_T0, d01 + d02))),
        daily_url(2026, 8, 2): (200, _zip_bytes(
            _cli_csv(CLI_T0, d02, close_delta=1.0))),  # 同 time 不同 close → 冲突
    }))
    with pytest.raises(ValueError, match="内容冲突"):
        _cli_fetch(tmp_path)


# ── 9.4 带/不带表头：月界首根不丢 ───────────────────────────────────────────

def test_cli_fetch_header_no_header_first_bars_kept(monkeypatch, tmp_path):
    headers = {monthly_url(2026, 7), daily_url(2026, 8, 2)}
    _use_fake_http(monkeypatch, _cli_bodies(header_urls=headers))
    res_hdr = _cli_fetch(tmp_path / "hdr")
    # 无表头变体：重新 patch 同一批 fake 字节（不带表头）
    _use_fake_http(monkeypatch, _cli_bodies())
    res_nohdr = _cli_fetch(tmp_path / "nohdr")
    assert res_hdr["bar_count"] == res_nohdr["bar_count"] == CLI_N
    # 表头存在与否 → 解析结果一致（首根绝不因表头/无表头丢失）
    assert (tmp_path / "hdr" / RAW_4H).read_bytes() == \
        (tmp_path / "nohdr" / RAW_4H).read_bytes()

    raw = read_gzip_json(tmp_path / "hdr" / RAW_4H)
    assert len(raw) == CLI_N
    assert [b["time"] for b in raw] == [CLI_T0 + i * STEP4 for i in range(CLI_N)]
    # 月界首根 2026-07-01（带表头）、日界 2026-08-01（无表头）、2026-08-02（带表头）
    for t in (
        CLI_T0,
        int(datetime.datetime(2026, 8, 1, tzinfo=datetime.timezone.utc).timestamp()),
        int(datetime.datetime(2026, 8, 2, tzinfo=datetime.timezone.utc).timestamp()),
    ):
        idx = (t - CLI_T0) // STEP4
        assert raw[idx]["time"] == t
        assert raw[idx]["close"] == pytest.approx(_cli_close(idx))
    assert raw[-1]["time"] == CLI_LAST


# ── 9.5 确定性 gzip / 清单（可重复 fetch 覆盖生成相同字节）──────────────────

def test_cli_fetch_deterministic_repeat_and_manifest(monkeypatch, tmp_path):
    _use_fake_http(monkeypatch, _cli_bodies())
    r1 = _cli_fetch(tmp_path / "a")
    r2 = _cli_fetch(tmp_path / "a")   # 同目录覆盖重跑
    assert r1 == r2
    assert r1["source_urls"] == len(CLI_URLS)   # source_urls 反映实际传入 urls
    _cli_fetch(tmp_path / "b")        # 不同目录
    for name in (RAW_4H, DERIVED_1DAY, DERIVED_1WEEK,
                 SOURCE_MANIFEST, SHA_MANIFEST):
        assert (tmp_path / "a" / name).read_bytes() == \
            (tmp_path / "b" / name).read_bytes()
    # fetch_metadata 除外：fetched_at 每次不同，且不进确定性清单
    assert (tmp_path / "a" / FETCH_METADATA).read_bytes() != \
        (tmp_path / "b" / FETCH_METADATA).read_bytes()
    assert "fetched_at" not in (tmp_path / "a" / SOURCE_MANIFEST).read_text("utf-8")
    assert "fetched_at" not in (tmp_path / "a" / SHA_MANIFEST).read_text("ascii")

    manifest = _cli_manifest(tmp_path / "a")
    assert manifest["requested_cutoff"] == "2026-08-16T00:00:00Z"
    assert manifest["raw_4h"]["count"] == CLI_N
    assert manifest["raw_4h"]["first"]["time"] == CLI_T0
    assert manifest["raw_4h"]["last"]["time"] == CLI_LAST
    assert manifest["raw_4h"]["last"]["close"] == pytest.approx(_cli_close(CLI_N - 1))
    assert [r["url"] for r in manifest["sources"]] == CLI_URLS  # 清单 sources 反映实际 urls
    assert len(manifest["sources"]) == len(CLI_URLS)
    assert all(r["ok"] and r["http_status"] == 200
               and len(r["content_sha256"]) == 64 for r in manifest["sources"])
    for name, sha in manifest["files"].items():
        assert hashlib.sha256((tmp_path / "a" / name).read_bytes()).hexdigest() == sha
    # MANIFEST.sha256 == source_manifest.json 字节的 sha256
    assert (tmp_path / "a" / SHA_MANIFEST).read_text("ascii").strip() == \
        hashlib.sha256((tmp_path / "a" / SOURCE_MANIFEST).read_bytes()).hexdigest() \
        + "  source_manifest.json"
    # 三份 gzip 的 gzip 头 mtime=0（确定性 gzip）
    for name in (RAW_4H, DERIVED_1DAY, DERIVED_1WEEK):
        assert (tmp_path / "a" / name).read_bytes()[4:8] == b"\x00\x00\x00\x00"


# ── 9.6 verify：重算 sha / 解 gzip / 连续性 / 聚合一致性（防篡改）──────────

def test_cli_verify_valid_passes(monkeypatch, tmp_path):
    _use_fake_http(monkeypatch, _cli_bodies())
    _cli_fetch(tmp_path)
    res = verify(tmp_path)
    assert res["valid"] is True, res["errors"]
    assert res["errors"] == []


def test_cli_verify_gzip_sha_tamper_fails(monkeypatch, tmp_path):
    _use_fake_http(monkeypatch, _cli_bodies())
    _cli_fetch(tmp_path)
    raw = bytearray((tmp_path / RAW_4H).read_bytes())
    raw[len(raw) // 2] ^= 0xFF            # 篡改压缩字节
    (tmp_path / RAW_4H).write_bytes(bytes(raw))
    res = verify(tmp_path)
    assert res["valid"] is False
    assert any("sha256 不一致" in e for e in res["errors"])


def test_cli_verify_manifest_tamper_fails(monkeypatch, tmp_path):
    _use_fake_http(monkeypatch, _cli_bodies())
    _cli_fetch(tmp_path)
    p = tmp_path / SOURCE_MANIFEST
    p.write_text(
        p.read_text("utf-8").replace(
            '"requested_cutoff":"2026-08-16T00:00:00Z"',
            '"requested_cutoff":"2099-01-01T00:00:00Z"',
        ), encoding="utf-8",
    )  # 改清单但不更新 MANIFEST.sha256
    res = verify(tmp_path)
    assert res["valid"] is False
    assert any("MANIFEST.sha256 与 source_manifest.json 不一致" in e
               for e in res["errors"])


def test_cli_verify_aggregation_inconsistent_fails(monkeypatch, tmp_path):
    _use_fake_http(monkeypatch, _cli_bodies())
    _cli_fetch(tmp_path)
    # 篡改 derived_1day 并“重新签名”（sha 层通过）→ 聚合一致性层必须拦截
    raw = read_gzip_json(tmp_path / RAW_4H)
    day = aggregate_utc(raw, "1day")
    day[-1]["close"] = round(float(day[-1]["close"]) + 1.0, 6)
    new_sha = write_gzip_json_deterministic(tmp_path / DERIVED_1DAY, day)
    man_path = tmp_path / SOURCE_MANIFEST
    manifest = json.loads(man_path.read_text(encoding="utf-8"))
    manifest["files"][DERIVED_1DAY] = new_sha
    man_bytes = (json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False) + "\n").encode("utf-8")
    man_path.write_bytes(man_bytes)
    (tmp_path / SHA_MANIFEST).write_bytes(
        (hashlib.sha256(man_bytes).hexdigest() + "  source_manifest.json\n").encode("ascii")
    )
    res = verify(tmp_path)
    assert res["valid"] is False
    assert any("1day 聚合不一致" in e for e in res["errors"])
    assert not any("sha256 不一致" in e for e in res["errors"])  # 确系聚合层拦截


# ── 9.7 run wrapper：verify 拒绝 / 确定性 manifest ──────────────────────────────

def test_cli_run_verify_failure_rejected(monkeypatch, tmp_path):
    """fetch 后篡改 raw gzip 1 字节 → run 必须 verify 失败拒绝（ValueError 含
    “verify 失败”），输出目录不得出现 RUN_MANIFEST（run 先 verify 后写产物）。"""
    _use_fake_http(monkeypatch, _cli_bodies())
    data = tmp_path / "data"
    _cli_fetch(data)
    raw = bytearray((data / RAW_4H).read_bytes())
    raw[len(raw) // 2] ^= 0xFF            # 篡改压缩字节 1 字节
    (data / RAW_4H).write_bytes(bytes(raw))
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="verify 失败"):
        run(data, out, expected_first_time=CLI_T0,
            expected_last_time=CLI_LAST)
    # 拒绝发生在写产物之前
    assert not (out / RUN_MANIFEST).exists()
    assert not (out / RUN_EVENTS).exists()
    assert not (out / RUN_SHA).exists()
    # 根因确系 verify 层（run 的拒绝来自 verify 失败）
    v = verify(data)
    assert v["valid"] is False


def test_cli_run_deterministic_manifest(monkeypatch, tmp_path):
    """同一 data 分别 run 到 out1/out2：五份产物字节一致；manifest 各项精确断言。"""
    _use_fake_http(monkeypatch, _cli_bodies())
    data = tmp_path / "data"
    _cli_fetch(data)
    out1, out2 = tmp_path / "out1", tmp_path / "out2"
    r1 = run(data, out1, expected_first_time=CLI_T0,
             expected_last_time=CLI_LAST)
    r2 = run(data, out2, expected_first_time=CLI_T0,
             expected_last_time=CLI_LAST)
    for name in (RUN_EVENTS, RUN_DECISIONS, RUN_SUMMARY,
                 RUN_MANIFEST, RUN_SHA):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes()
    # run 返回摘要除 output_dir 外字节级一致
    assert {k: v for k, v in r1.items() if k != "output_dir"} == \
        {k: v for k, v in r2.items() if k != "output_dir"}

    manifest = json.loads((out1 / RUN_MANIFEST).read_text(encoding="utf-8"))
    # ── schema / product / market / interval ──
    assert manifest["schema"] == "mtf-btc-backtest-v1.run-manifest.v1"
    assert manifest["product"] == "BINANCE:BTCUSDT"
    assert manifest["market"] == "Binance USD-M perpetual"
    assert manifest["interval"] == "4h"

    # ── data：source/raw sha == 文件真实 sha；bar 统计与 raw 一致 ──
    dblock = manifest["data"]
    assert dblock["source_manifest_sha256"] == hashlib.sha256(
        (data / SOURCE_MANIFEST).read_bytes()).hexdigest()
    assert dblock["raw_sha256"] == hashlib.sha256(
        (data / RAW_4H).read_bytes()).hexdigest()
    raw = read_gzip_json(data / RAW_4H)
    slim = ("time", "open", "high", "low", "close", "volume")
    assert dblock["bar_count"] == len(raw)
    assert dblock["first_bar"] == {k: raw[0][k] for k in slim}
    assert dblock["last_bar"] == {k: raw[-1][k] for k in slim}
    assert dblock["end_exclusive_time"] == int(raw[-1]["time"]) + 14400
    assert dblock["entry_basis"] == "next_4h_open_price"
    # 正式边界（run 参数）进清单：小样本显式 CLI_T0/CLI_LAST
    assert dblock["expected_bounds"] == {"first_time": CLI_T0, "last_time": CLI_LAST}

    # ── strategies / parameters ──
    assert manifest["strategies"] == ["ma", "structure", "macd"]
    assert manifest["parameters"] == {}

    # ── market_state_config：与 DEFAULT 公开字段精确一致 + hash 匹配 ──
    expected_cfg = {k: v for k, v in DEFAULT_MARKET_STATE_CONFIG.__dict__.items()
                    if not k.startswith("_")}
    assert manifest["market_state_config"] == expected_cfg
    assert manifest["market_state_config_hash"] == \
        DEFAULT_MARKET_STATE_CONFIG.config_hash()

    # ── policy A：human/rule 明确周+日都必须同向；policy B：日同向、周只 ready+closed ──
    #    规则串必须与实际 classify_policies 实现一致（含 trigger.is_closed）。
    pa, pb = manifest["policy_A"], manifest["policy_B"]
    assert "周线" in pa["human"] and "日线" in pa["human"] and "同向" in pa["human"]
    assert pa["rule"] == EXPECTED_POLICY_A_RULE
    assert "日线" in pb["human"] and "同向" in pb["human"]
    assert "ready+closed" in pb["human"] and "方向不限" in pb["human"]
    assert pb["rule"] == EXPECTED_POLICY_B_RULE
    # A 严格多一个周线同向约束；两者都必须含 trigger.is_closed
    assert "trigger.is_closed" in pa["rule"] and "trigger.is_closed" in pb["rule"]
    assert "week.trend == event_direction" in pa["rule"]
    assert "week.trend == event_direction" not in pb["rule"]

    # ── methodology / costs / windows ──
    assert manifest["methodology"] == "event_study"
    assert manifest["costs"] == {"fees": 0.0, "slippage": 0.0}
    assert manifest["outcome_windows"] == [5, 10, 20, 40]
    assert manifest["outcome_window_unit"] == "completed_4h_bars"

    # ── outputs：三个 sha 与产物字节一致 ──
    for name in (RUN_EVENTS, RUN_DECISIONS, RUN_SUMMARY):
        assert manifest["outputs"][name] == hashlib.sha256(
            (out1 / name).read_bytes()).hexdigest()

    # ── counts：与 events.csv / decisions.csv 实际行数一致（0 事件也正确）──
    with open(out1 / RUN_EVENTS, newline="", encoding="utf-8") as f:
        ev_rows = list(csv.DictReader(f))
    unique_keys = len({r["event_key"] for r in ev_rows})
    assert manifest["counts"]["event_count"] == unique_keys
    assert manifest["counts"]["events_rows"] == len(ev_rows)
    with open(out1 / RUN_DECISIONS, newline="", encoding="utf-8") as f:
        dec_rows = list(csv.DictReader(f))
    assert len(dec_rows) % 4 == 0  # 每 decision 恒 4 窗口
    assert manifest["counts"]["decision_count"] == len(dec_rows) // 4
    # 与 run 返回摘要同口径
    assert r1["event_count"] == unique_keys
    assert r1["events_rows"] == len(ev_rows)
    assert r1["decision_count"] == len(dec_rows) // 4

    # ── RUN_MANIFEST.sha256 内容正确（sha256sum 格式）──
    assert (out1 / RUN_SHA).read_text("ascii").strip() == \
        hashlib.sha256((out1 / RUN_MANIFEST).read_bytes()).hexdigest() \
        + "  " + RUN_MANIFEST

    # ── summary 确系从 events.csv 复算 ──
    summary = json.loads((out1 / RUN_SUMMARY).read_text(encoding="utf-8"))
    assert summary["generated_from"] == "events.csv"


def test_cli_run_default_formal_bounds_rejects_small_sample(monkeypatch, tmp_path):
    """合法小样本（fetch/verify 都通过）但不传边界覆盖 → run 默认正式边界
    （2020-01-01T00:00Z .. 2026-08-14T20:00Z）不匹配 → 必须拒绝（ValueError
    含缺头/缺尾），即便 verify 通过；拒绝发生在写产物之前。"""
    _use_fake_http(monkeypatch, _cli_bodies())
    data = tmp_path / "data"
    _cli_fetch(data)
    assert verify(data)["valid"] is True
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="缺头|缺尾"):
        run(data, out)  # 不传覆盖 → 严格正式边界
    assert not (out / RUN_MANIFEST).exists()
    assert not (out / RUN_EVENTS).exists()
    assert not (out / RUN_SHA).exists()


def test_cli_run_product_tamper_resigned_verify_ok_run_rejects(monkeypatch, tmp_path):
    """篡改 source_manifest.product 为 ETHUSDT 并重新生成 MANIFEST.sha256
    → verify 通过（sha/连续性/聚合全部一致）但 run 因产品不匹配拒绝
    （即使边界显式给小样本）；拒绝发生在写产物之前。"""
    _use_fake_http(monkeypatch, _cli_bodies())
    data = tmp_path / "data"
    _cli_fetch(data)
    man_path = data / SOURCE_MANIFEST
    manifest = json.loads(man_path.read_text(encoding="utf-8"))
    assert manifest["product"] == "BTCUSDT"
    manifest["product"] = "ETHUSDT"
    man_bytes = (json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False) + "\n").encode("utf-8")
    man_path.write_bytes(man_bytes)
    (data / SHA_MANIFEST).write_bytes(
        (hashlib.sha256(man_bytes).hexdigest()
         + "  source_manifest.json\n").encode("ascii")
    )
    v = verify(data)
    assert v["valid"] is True, v["errors"]  # 哈希/连续性/聚合全部通过
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="product"):
        run(data, out, expected_first_time=CLI_T0,
            expected_last_time=CLI_LAST)
    assert not (out / RUN_MANIFEST).exists()
    assert not (out / RUN_EVENTS).exists()
