"""M1 legacy parity 测试：旧入口 golden 逐字段不变。

四组确定性序列（上涨/下跌/震荡/波动周期）的旧入口输出在**修改前**由旧代码
生成并冻结为以下常量（生成器 /tmp/gen_m1_golden.py，序列定义与本文件逐字一致；
被冻结文件 SHA-256 见 PROGRESS.md M1 任务 0）。本测试断言迁移后输出与冻结值
完全一致——预期值不随新实现重算，禁止改预期迁就实现。

覆盖：
  - trend_classifier.classify 全数组 digest
  - regime_classifier regime ID / alignment digest
  - 日报 InstrumentAnalysis.model_dump digest 与 stage/phase
  - 策略回测摘要 digest（含 by_strategy / by_regime）
  - 回测 fast/slow 路径等价
"""
from __future__ import annotations

import math

import pytest

from tests.golden_helpers import digest

from app.core.trend_classifier import classify
from app.core.regime_classifier import precompute_regime_arrays
from app.core.daily_report_analysis import analyze_instrument
from app.core.strategy_backtest_engine import StrategyBacktester
from app.core.position_sizing import AccountConfig
from app.core.indicators import _atr
from app.core.regime_classifier import precompute_regime_arrays as _pre

_START = 1_700_000_000
_STEP = 3600


def make_series(n, drift_fn, wave_fn, base=100.0):
    out = []
    for i in range(n):
        d = drift_fn(i)
        w = wave_fn(i)
        close = base * d * w
        openp = base * drift_fn(i - 1) * wave_fn(i - 1) if i else close
        out.append({
            "time": _START + i * _STEP,
            "open": round(openp, 6),
            "high": round(max(openp, close) * 1.012, 6),
            "low": round(min(openp, close) * 0.988, 6),
            "close": round(close, 6),
            "volume": 1000 + 50 * ((i * 37) % 11),
        })
    return out


def series_up(n=500):
    return make_series(n, lambda i: 1 + 0.002 * i, lambda i: 1 + 0.05 * math.sin(i / 7.0) + 0.02 * math.sin(i / 2.9))


def series_down(n=500):
    return make_series(n, lambda i: 1 - 0.0015 * i, lambda i: 1 + 0.05 * math.sin(i / 7.0) + 0.02 * math.sin(i / 2.9))


def series_sideways(n=500):
    return make_series(n, lambda i: 1.0, lambda i: 1 + 0.04 * math.sin(i / 9.0) + 0.015 * math.sin(i / 3.1))


def series_vol_cycle(n=500):
    def wave(i):
        amp = 0.02 + 0.06 * (0.5 + 0.5 * math.sin(i / 70.0))
        return 1 + amp * math.sin(i / 4.0)
    return make_series(n, lambda i: 1 + 0.0006 * i, wave)


# ── 冻结 golden（修改前旧代码输出，2026-08-13 生成）────────────────────────────
# 四元组：序列构造器
GOLDEN_SERIES = [
    pytest.param(series_up, "up", id="up"),
    pytest.param(series_down, "down", id="down"),
    pytest.param(series_sideways, "sideways", id="sideways"),
    pytest.param(series_vol_cycle, "vol_cycle", id="vol_cycle"),
]

GOLDEN = {
    "up": {
        "trend_array_digest": "b4f71f69c2d7c6ea",
        "regime_ids_digest": "a8127c377a3d1935",
        "regime_alignments_digest": "5f7a2f8afbb7a5c1",
        "daily_model_dump_digest": "5e5308c50bd1aded",
        "daily_stage": "上涨趋势",
        "daily_tactical_phase": "continuation",
        "bt_summary_digest": "4add623ba2770b8c",
        "bt_total_trades": 62,
    },
    "down": {
        "trend_array_digest": "b620dcb064905292",
        "regime_ids_digest": "1a7121a8e206b744",
        "regime_alignments_digest": "d7b66634a7df3b4b",
        "daily_model_dump_digest": "d3ff32e27fc5ed38",
        "daily_stage": "下跌趋势",
        "daily_tactical_phase": "continuation",
        "bt_summary_digest": "37c16af308e17fdf",
        "bt_total_trades": 50,
    },
    "sideways": {
        "trend_array_digest": "6d40c3bcd9a95f02",
        "regime_ids_digest": "b5ae27bbc45c83c5",
        "regime_alignments_digest": "1ac0a1f7b62052a7",
        "daily_model_dump_digest": "d263c3c7bdc476af",
        "daily_stage": "趋势不明确",
        "daily_tactical_phase": "unclear",
        "bt_summary_digest": "5a5c9bb987a9bd90",
        "bt_total_trades": 113,
    },
    "vol_cycle": {
        "trend_array_digest": "93a37090d636a580",
        "regime_ids_digest": "b4938fa503518d70",
        "regime_alignments_digest": "fbdb37aae24fd58b",
        "daily_model_dump_digest": "b8e32336c0fcffc5",
        "daily_stage": "趋势不明确",
        "daily_tactical_phase": "unclear",
        "bt_summary_digest": "4585b19dc8e94aea",
        "bt_total_trades": 40,
    },
}


def _bt_summary(report) -> dict:
    return {
        "total_return_pct": report.total_return_pct,
        "max_drawdown_pct": report.max_drawdown_pct,
        "sharpe_ratio": report.sharpe_ratio,
        "total_trades": report.total_trades,
        "win_rate_pct": report.win_rate_pct,
        "by_strategy": report.by_strategy,
        "by_regime": report.by_regime,
    }


@pytest.mark.parametrize("series_fn,name", GOLDEN_SERIES)
def test_legacy_entries_match_frozen_golden(series_fn, name):
    """三个旧入口 + 回测输出必须与冻结 golden 逐字段一致（含 ID/标签/文字）。"""
    candles = series_fn(n=500)
    golden = GOLDEN[name]

    # 1) trend_classifier 全数组
    assert digest(classify(candles)) == golden["trend_array_digest"]

    # 2) regime ID / alignment
    ids, aligns, _ = precompute_regime_arrays(candles)
    assert digest(ids) == golden["regime_ids_digest"]
    assert digest(aligns) == golden["regime_alignments_digest"]

    # 3) 日报 model_dump（含 stage/tactical_phase/conclusion 文字）
    ia = analyze_instrument("SYN", "SYNUSD", ["crypto"], candles, session_timezone="Asia/Shanghai")
    assert digest(ia.model_dump()) == golden["daily_model_dump_digest"]
    assert ia.stage == golden["daily_stage"]
    assert ia.tactical_phase == golden["daily_tactical_phase"]

    # 4) 策略回测摘要（含 by_strategy / by_regime）
    report = StrategyBacktester(AccountConfig(initial_capital=1000.0)).run(candles, "SYN", "1day")
    assert digest(_bt_summary(report)) == golden["bt_summary_digest"]
    assert report.total_trades == golden["bt_total_trades"]


@pytest.mark.parametrize("series_fn,name", GOLDEN_SERIES)
def test_backtest_fast_slow_equivalent(series_fn, name):
    """回测 fast/slow 路径在四组冻结序列上产生相同的 (strategy, direction, regime_id, regime_caution)。"""
    candles = series_fn(n=500)
    tester = StrategyBacktester()
    trend_arr = tester.trend_detector.signal_array(candles, {})
    momentum_arr = tester.momentum_detector.signal_array(candles, {})
    reversal_arr = tester.reversal_detector.signal_array(candles, {})
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    atr14 = _atr(highs, lows, closes, 14)
    pre_ids, pre_align, _ = _pre(candles)

    compared = 0
    for i in range(100, len(candles)):
        for arr, stype in [
            (trend_arr, "trend"),
            (momentum_arr, "momentum"),
            (reversal_arr, "reversal"),
        ]:
            if i >= len(arr) or arr[i] == 0:
                continue
            fast = tester._collect_signals_fast(
                i, trend_arr, momentum_arr, reversal_arr,
                "SYN", "1day", closes, highs, lows, atr14, pre_ids, pre_align,
            )
            slow = tester._collect_signals(
                i, candles, trend_arr, momentum_arr, reversal_arr,
                "SYN", "1day", closes, highs, lows, atr14,
            )
            fast_key = {(o.strategy_type, o.direction, o.regime_id, o.regime_caution) for o in fast}
            slow_key = {(o.strategy_type, o.direction, o.regime_id, o.regime_caution) for o in slow}
            assert fast_key == slow_key, f"[{name}] i={i} {stype}: fast={fast_key} slow={slow_key}"
            compared += 1
    assert compared > 0, f"[{name}] 未触发任何信号，等价性检查为空转"
