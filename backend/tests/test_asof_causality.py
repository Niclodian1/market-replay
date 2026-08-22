"""as-of 因果一致性测试（告警体系 V2 · 里程碑 0 · Task 2）。

统一 as-of 契约：
  1. 时刻 t 的状态/信号只能依赖 candles[:t+1]。
  2. 中心摆动点只有经过右侧 pivot 根（idx + pivot <= t）后才确认可用。
  3. 信号只记在确认/决策 bar，禁止回写历史 bar。

验收口径（本文件为准）：
  - 对确定性 OHLC 序列逐前缀比较「完整历史数组」：
    对每个足够长的前缀 k，比较 component(candles[:k+1]) 在 [start, k] 上的全部值
    与 component(candles) 在同一区间的值，必须完全一致——
    即：追加任何未来 K 线都不得改变已产生的结果。
  - 前缀长度 k 必须达到组件的「最小数据要求」（warmup），否则组件按契约拒绝
    输出（guard），该区域不属于「已产生结果」，不计入比较。
  - Reversal：先创新低/新高、再收回的事件，信号必须落在确认 bar，禁止回填。
  - 快慢回测路径必须产生相同的 regime_caution。
"""
from __future__ import annotations

import pytest

from tests.golden_helpers import make_candles

from app.core.trend_classifier import classify
from app.core.signals.strategy_detectors import (
    TrendStrategyDetector,
    MomentumStrategyDetector,
    ReversalStrategyDetector,
)
from app.core.strategy_backtest_engine import StrategyBacktester
from app.core.indicators import _atr
from app.core.regime_classifier import precompute_regime_arrays


# ── 每个组件的比较窗口 ─────────────────────────────────────────────────────────
# warmup: 组件产生有效输出所需的最短前缀长度（小于该长度按契约返回默认值）
# start:  组件可能产生非默认输出的第一个 index
COMPONENTS = [
    pytest.param(classify, 65, 60, None, id="trend_classifier"),
    pytest.param(TrendStrategyDetector().signal_array, 120, 101, {}, id="trend_detector"),
    pytest.param(MomentumStrategyDetector().signal_array, 120, 120, {}, id="momentum_detector"),
    pytest.param(ReversalStrategyDetector().signal_array, 80, 80, {}, id="reversal_detector"),
]


def _prefix_consistency(compute, candles, warmup, start, params):
    """逐前缀比较完整历史数组；返回 (检查数, 不一致数, 首个不一致样本)。"""
    full = compute(candles, params) if params is not None else compute(candles)
    n = len(candles)
    checked = 0
    mismatches = 0
    first_bad = None
    for k in range(warmup, n):
        prefix_out = compute(candles[:k + 1], params) if params is not None else compute(candles[:k + 1])
        for j in range(start, k + 1):
            checked += 1
            if prefix_out[j] != full[j]:
                mismatches += 1
                if first_bad is None:
                    first_bad = (k, j, prefix_out[j], full[j])
    return checked, mismatches, first_bad


@pytest.mark.parametrize("compute,warmup,start,params", COMPONENTS)
@pytest.mark.parametrize("base", [100.0, 500.0, 1000.0])
def test_prefix_consistency_full_history_array(compute, warmup, start, params, base):
    """追加任何未来 K 线都不得改变已产生的结果（前缀不一致数必须为 0）。"""
    candles = make_candles(n=500, base=base)
    checked, mismatches, first_bad = _prefix_consistency(compute, candles, warmup, start, params)
    assert checked > 0, "探测必须实际检查到前缀"
    assert mismatches == 0, (
        f"前缀不一致 {mismatches}/{checked}，首个样本 k={first_bad[0]} j={first_bad[1]} "
        f"prefix={first_bad[2]} full={first_bad[3]}（追加未来 K 线改变了已产生结果）"
    )


# ── Reversal：禁止向历史 bar 回填信号 ─────────────────────────────────────────

def _reversal_backfill_candles(n: int = 130) -> list[dict]:
    """构造「新低 + 提前收回」场景。

    - bar 80 的 low 是 20 根窗口的最低点 L=97.5
    - bar 98: 看涨 pinbar（long lower wick），close=101 > L（收回），但 low=97.9 > L（非新低）
    - bar 99: low=96.5 < L（新低），close=97.0 <= L（bar 99 自身未收回）
    - 确认 bar = 99：此时「新低 + 窗口内收回 + 形态」首次可验证
    旧实现会把信号回填到 bar 98（未来数据）；修复后必须落在 bar 99。
    """
    candles = []
    ts = 1_700_000_000
    for i in range(n):
        candles.append({
            "time": ts + i * 3600,
            "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 100.0,
        })
    for i in range(0, 81):
        o = 101.0 - i * 0.02
        lo = 100.6 - i * 0.02
        candles[i].update(open=o, high=o + 0.2, low=lo, close=o - 0.01)
    candles[80].update(low=97.5, open=98.2, high=98.4, close=98.0)
    for i in range(81, 98):
        candles[i].update(open=98.2, high=98.5, low=97.8, close=98.1)
    candles[98].update(open=100.0, high=101.2, low=97.9, close=101.0)
    candles[99].update(open=97.8, high=98.0, low=96.5, close=97.0)
    for i in range(100, n):
        candles[i].update(open=98.0, high=98.3, low=97.6, close=98.1)
    return candles


def test_reversal_signal_emitted_at_confirmation_bar_not_backfilled():
    """信号必须落在确认 bar，不得回填到更早的收回 bar。"""
    candles = _reversal_backfill_candles()
    arr = ReversalStrategyDetector().signal_array(candles, {})
    fired = [i for i, v in enumerate(arr) if v != 0]
    # 旧实现: [98]（回填）；修复后: [99]（确认 bar）
    assert fired == [99], f"期望信号只在确认 bar 99，实际 {fired}"


def test_reversal_no_signal_before_confirmation():
    """在确认 bar 之前（前缀只到 98）不得出现任何信号（包括 index 98）。"""
    candles = _reversal_backfill_candles()
    det = ReversalStrategyDetector()
    for k in (80, 90, 98):
        arr = det.signal_array(candles[:k + 1], {})
        assert all(v == 0 for v in arr), f"前缀 {k+1} 不应产生任何信号"
    arr99 = det.signal_array(candles[:100], {})
    assert arr99[99] != 0, "前缀到 99 时应已在确认 bar 99 产生信号"


# ── Trend：摆动点必须已确认（confirmed_at <= 当前 bar）────────────────────────

def _trend_unconfirmed_swing_candles(n: int = 130) -> list[dict]:
    """构造「未确认摆动点」场景。

    - 84 号 bar: 摆动高点 H1=110（已确认）
    - 93 号 bar: 摆动高点 108（已确认）
    - 104 号 bar: 摆动高点 105（在 105 号决策 bar 时**尚未确认**：104+3=107 > 105）
    - 105 号 bar: 收盘 104.9 恰好突破「含 104 号摆动点」的下降趋势线（旧实现会用
      未确认摆动点在此触发），但未突破「仅已确认摆动点」的趋势线
    旧实现: 在 105 触发（泄漏）；修复后: 105 不得触发，只有 107（摆动点已确认）触发。
    """
    candles = []
    ts = 1_700_000_000
    for i in range(n):
        candles.append({
            "time": ts + i * 3600,
            "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 100.0,
        })

    def bar(i, o, h, l, c):
        candles[i].update(open=o, high=h, low=l, close=c)

    # 早期深坑（振幅 > 20%，跳过横盘突破分支）
    bar(10, 85.0, 100.0, 70.0, 88.0)
    # 0..83: 高点缓慢抬升且 <= 110（不产生高于 H1 的摆动高点）
    for i in range(0, 84):
        h = 96.0 + i * 0.166
        bar(i, h - 0.8, h, h - 2.0, h - 1.0)
    bar(84, 109.0, 110.0, 107.0, 109.2)          # s1 = 摆动高点 110
    for i in range(85, 93):
        h = 109.6 - (i - 85) * 0.4
        bar(i, h - 0.6, h, h - 1.8, h - 0.8)
    bar(93, 107.0, 108.0, 105.0, 107.4)          # 摆动高点 108
    bar(94, 106.5, 107.5, 105.2, 106.8)          # 触碰点（非摆动高点）
    for i in range(95, 101):
        h = 106.5 - (i - 95) * 0.25
        bar(i, h - 0.5, h, h - 1.5, h - 0.6)
    for i in range(101, 104):
        h = 104.8 - (i - 101) * 0.1
        bar(i, h - 0.4, h, h - 1.2, h - 0.5)
    bar(104, 104.5, 105.0, 104.3, 104.7)         # s2 = 摆动高点 105（105 时未确认）
    bar(105, 104.5, 104.95, 104.4, 104.9)        # 决策 bar
    for i in range(106, n):
        bar(i, 104.4, 104.8 - (i - 106) * 0.05, 104.0, 104.5)
    return candles


def test_trend_no_signal_on_unconfirmed_swing():
    """未确认摆动点（idx + pivot > 决策 bar）不得触发趋势线突破。"""
    candles = _trend_unconfirmed_swing_candles()
    arr = TrendStrategyDetector().signal_array(candles, {})
    fired = [i for i, v in enumerate(arr) if v != 0]
    # 旧实现: 在 105 触发（使用 104 号未确认摆动点）；修复后 105 必须为 0
    assert 105 not in fired, f"bar 105 使用了未确认摆动点（104+3>105），实际触发于 {fired}"
    assert fired, "修复后仍应在摆动点确认后（107）触发"


def test_trend_unconfirmed_swing_series_prefix_consistent():
    """同一构造序列也必须满足逐前缀完整历史数组一致性。"""
    candles = _trend_unconfirmed_swing_candles()
    checked, mismatches, first_bad = _prefix_consistency(
        TrendStrategyDetector().signal_array, candles, 120, 101, {},
    )
    assert checked > 0
    assert mismatches == 0, f"前缀不一致 {mismatches}，首个 {first_bad}"


def test_trend_uses_only_confirmed_swings():
    """趋势线子信号只能使用 idx + pivot <= i 的摆动点（契约保护）。

    注：摆动点集合在前缀与全序列中天然一致（窗口相同），故前缀一致性测试
    无法单独暴露该泄漏；这里通过 _touches_line(current_idx) 拿到处理中的 bar，
    断言每次趋势线拟合所用的摆动点全部满足 idx + pivot <= 当前 bar。
    """
    import app.core.signals.strategy_detectors as sd

    candles = make_candles(n=300, base=100.0)
    violations: list[tuple[int, int, int]] = []
    checked = 0
    pending_fits: list[list[int]] = []

    orig_fit = sd._fit_line
    orig_touch = sd.TrendStrategyDetector._touches_line

    def recording_fit(xs, ys):
        pending_fits.append([int(x) for x in xs])
        return orig_fit(xs, ys)

    def checking_touch(prices, slope, intercept, current_idx, lookback):
        nonlocal checked
        while pending_fits:
            idxs = pending_fits.pop(0)
            for idx in idxs:
                checked += 1
                if idx + 3 > current_idx:  # pivot=3：右侧 pivot 根未走完即违规
                    violations.append((idx, current_idx, idx + 3))
        return orig_touch(prices, slope, intercept, current_idx, lookback)

    sd._fit_line = recording_fit
    sd.TrendStrategyDetector._touches_line = staticmethod(checking_touch)
    try:
        TrendStrategyDetector().signal_array(candles, {})
    finally:
        sd._fit_line = orig_fit
        sd.TrendStrategyDetector._touches_line = staticmethod(orig_touch)

    assert checked > 0, "探测必须捕获到趋势线拟合调用"
    assert not violations, (
        f"使用了未确认摆动点 {len(violations)} 处，样本: "
        f"(swing_idx={violations[0][0]}, bar={violations[0][1]}, confirmed_at={violations[0][2]})"
    )


# ── 快慢回测路径 regime_caution 等价 ──────────────────────────────────────────

def test_fast_and_slow_paths_produce_same_regime_caution():
    """快速路径（预计算 regime 查表）与慢速路径（逐 bar classify）必须产生相同的
    regime_caution（修复前快速路径固定为 False）。"""
    candles = make_candles(n=500, base=100.0)
    tester = StrategyBacktester()
    trend_arr = tester.trend_detector.signal_array(candles, {})
    momentum_arr = tester.momentum_detector.signal_array(candles, {})
    reversal_arr = tester.reversal_detector.signal_array(candles, {})
    highs = [float(c["high"]) for c in candles]
    lows = [float(c["low"]) for c in candles]
    closes = [float(c["close"]) for c in candles]
    atr14 = _atr(highs, lows, closes, 14)
    pre_ids, pre_align, _ = precompute_regime_arrays(candles)

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
            assert fast_key == slow_key, f"i={i} {stype}: fast={fast_key} slow={slow_key}"
            compared += 1

    assert compared > 0, "探测必须实际触发过信号"
    # 必须至少出现过 caution=0.5 的订单，证明等价性不是「两边都是 False」的空转
    slow_all = []
    for i in range(100, len(candles)):
        for arr, stype in [
            (trend_arr, "trend"),
            (momentum_arr, "momentum"),
            (reversal_arr, "reversal"),
        ]:
            if i >= len(arr) or arr[i] == 0:
                continue
            slow_all.extend(tester._collect_signals(
                i, candles, trend_arr, momentum_arr, reversal_arr,
                "SYN", "1day", closes, highs, lows, atr14,
            ))
    assert any(o.regime_caution != 1.0 for o in slow_all), "探测必须覆盖 caution 降仓场景"
