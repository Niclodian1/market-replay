"""新架构 golden 测试。

锁住:
  - ADX / ATR 百分位 / MA 排列 计算
  - Regime 分类器输出
  - 三种策略信号数组
  - 仓位计算
  - 端到端策略回测
"""
import math

from app.core.indicators import _adx, _atr_percentile, _ma_alignment
from app.core.regime_classifier import (
    classify_regime_at, alignment_direction, direction_permitted, regime_permits,
)
from app.core.position_sizing import (
    AccountConfig, compute_position_size, allocate_exposure,
    build_signal_order, SignalOrder,
)
from app.core.signals.strategy_detectors import (
    TrendStrategyDetector, MomentumStrategyDetector, ReversalStrategyDetector,
)
from app.core.strategy_backtest_engine import StrategyBacktester, run_backtest
from tests.golden_helpers import digest, make_candles


# ═══════════════════════════════════════════════════════════════════════════════
#  ADX 指标
# ═══════════════════════════════════════════════════════════════════════════════

def test_adx_output_length():
    """ADX 返回与输入等长的序列。"""
    candles = make_candles(200)
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    result = _adx(highs, lows, closes)
    assert len(result) == len(candles)


def test_adx_initial_none():
    """ADX 前 period*2-1 根为 None。"""
    candles = make_candles(200)
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    result = _adx(highs, lows, closes, period=14)
    # 前 27 根应为 None
    for i in range(27):
        assert result[i] is None, f"index {i} should be None, got {result[i]}"
    # 第 28 根开始有值
    assert result[27] is not None


def test_adx_range():
    """ADX 值应在 0-100 之间。"""
    candles = make_candles(200)
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    result = _adx(highs, lows, closes)
    for v in result:
        if v is not None:
            assert 0 <= v <= 100, f"ADX out of range: {v}"


# ═══════════════════════════════════════════════════════════════════════════════
#  ATR 百分位
# ═══════════════════════════════════════════════════════════════════════════════

def test_atr_percentile_range():
    """ATR 百分位应在 0.0-1.0 之间。"""
    candles = make_candles(200)
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    closes = [c["close"] for c in candles]
    result = _atr_percentile(highs, lows, closes, lookback=100)
    for v in result:
        if v is not None:
            assert 0.0 <= v <= 1.0, f"ATR percentile out of range: {v}"


# ═══════════════════════════════════════════════════════════════════════════════
#  MA 排列
# ═══════════════════════════════════════════════════════════════════════════════

def test_ma_alignment_range():
    """MA 排列值应在 -2 到 +2 之间。"""
    candles = make_candles(200)
    closes = [c["close"] for c in candles]
    result = _ma_alignment(closes)
    for v in result:
        assert v in (-2, -1, 0, 1, 2), f"MA alignment out of range: {v}"


def test_ma_alignment_matches_intuition():
    """趋势向上时 MA 排列应为正。"""
    # 构造一个简单上升序列
    closes = [100.0 + i * 0.5 for i in range(200)]
    result = _ma_alignment(closes)
    # MA 排列稳定后应为正（价格 > MA快 > MA中 > MA慢 在强上升中成立）
    valid = [v for v in result if v != 0]
    # 在严格线性上升中，排列应为 +2
    positive = [v for v in valid if v > 0]
    assert len(positive) >= len(valid) * 0.8, f"Expected mostly positive, got {len(positive)}/{len(valid)}"


# ═══════════════════════════════════════════════════════════════════════════════
#  Regime 分类器
# ═══════════════════════════════════════════════════════════════════════════════

def test_classify_regime_returns_valid():
    """Regime 分类应返回 0-5 的有效 ID。"""
    candles = make_candles(200)
    regime = classify_regime_at(candles, len(candles) - 1)
    assert 0 <= regime.id <= 5, f"Invalid regime ID: {regime.id}"
    assert regime.trend in ("ranging", "weak_trend", "strong_trend")
    assert regime.volatility in ("low", "high")


def test_direction_permitted_logic():
    """方向过滤逻辑验证。"""
    # 偏多排列 → 只允许多头
    assert direction_permitted(2, "long") is True
    assert direction_permitted(2, "short") is False
    assert direction_permitted(1, "long") is True
    assert direction_permitted(1, "short") is False

    # 偏空排列 → 只允许空头
    assert direction_permitted(-2, "short") is True
    assert direction_permitted(-2, "long") is False

    # 缠绕组 → 双向
    assert direction_permitted(0, "long") is True
    assert direction_permitted(0, "short") is True


def test_regime_permits_trend():
    """趋势策略 Regime 权限（回测数据校准后）。"""
    # 弱趋势低波 → 允许，全仓位（主力环境）
    allowed, coeff = regime_permits(2, "trend")
    assert allowed is True
    assert coeff == 1.0

    # 震荡高波 → 禁止
    allowed, coeff = regime_permits(1, "trend")
    assert allowed is False
    assert coeff == 0.0

    # 强趋势低波 → caution（回测: BTC上负收益，从 allowed 降级）
    allowed, coeff = regime_permits(4, "trend")
    assert allowed is True
    assert coeff == 0.5

    # 震荡低波 → caution（回测: BTC上2笔全盈，开放尝试）
    allowed, coeff = regime_permits(0, "trend")
    assert allowed is True
    assert coeff == 0.5


def test_regime_permits_reversal():
    """反转策略 Regime 权限。"""
    # 震荡低波 → 主力，全仓位
    allowed, coeff = regime_permits(0, "reversal")
    assert allowed is True
    assert coeff == 1.0

    # 强趋势高波 → 唯一禁用（冲顶/恐慌时反转风险最高）
    allowed, coeff = regime_permits(5, "reversal")
    assert allowed is False
    # 强趋势低波 → caution（回测调整：放宽准入增加样本）
    allowed, coeff = regime_permits(4, "reversal")
    assert allowed is True
    assert coeff == 0.5


# ═══════════════════════════════════════════════════════════════════════════════
#  仓位计算
# ═══════════════════════════════════════════════════════════════════════════════

def test_compute_position_size_basic():
    """仓位计算: 标准场景。"""
    pos, risk = compute_position_size(
        account=1000.0,
        risk_budget_pct=2.0,
        entry_price=100.0,
        stop_loss=95.0,
        leverage=2.0,
    )
    # 价格风险 = 5%
    # 无杠杆仓位 = $20 / 5% = $400
    # 2× 杠杆 = $800
    assert pos == 800.0, f"Expected 800.0, got {pos}"
    assert risk == 40.0, f"Expected 40.0, got {risk}"


def test_compute_position_size_caution():
    """仓位计算: Regime 降仓位减半风险预算。"""
    pos, risk = compute_position_size(
        account=1000.0,
        risk_budget_pct=2.0,
        entry_price=100.0,
        stop_loss=95.0,
        leverage=2.0,
        regime_caution=True,
    )
    # 风险预算减半 = 1%
    # 无杠杆仓位 = $10 / 5% = $200
    # 2× = $400
    assert pos == 400.0, f"Expected 400.0, got {pos}"


def test_allocate_exposure_priority():
    """敞口分配: 趋势优先于动能优先于反转。"""
    orders = [
        build_signal_order("momentum", "BTC-USD", "long", 100.0, 95.0, "1day", 2),
        build_signal_order("trend", "ETH-USD", "long", 2000.0, 1900.0, "1day", 2),
        build_signal_order("reversal", "BTC-USD", "long", 100.0, 93.0, "1day", 0),
    ]
    account = AccountConfig(initial_capital=1000.0)
    result = allocate_exposure(orders, account)

    # 趋势应排第一
    assert result.orders[0].strategy_type == "trend"
    assert result.orders[0].filled is True


# ═══════════════════════════════════════════════════════════════════════════════
#  策略信号数组
# ═══════════════════════════════════════════════════════════════════════════════

def test_trend_detector_has_signal_array():
    """趋势探测器 signal_array 返回等长列表。"""
    candles = make_candles(300)
    det = TrendStrategyDetector()
    arr = det.signal_array(candles, {})
    assert len(arr) == len(candles)
    # 信号只能是 -1, 0, 1
    assert all(s in (-1, 0, 1) for s in arr)


def test_momentum_detector_has_signal_array():
    """动能探测器 signal_array 返回等长列表。"""
    candles = make_candles(300)
    det = MomentumStrategyDetector()
    arr = det.signal_array(candles, {})
    assert len(arr) == len(candles)
    assert all(s in (-1, 0, 1) for s in arr)


def test_reversal_detector_has_signal_array():
    """反转探测器 signal_array 返回等长列表。"""
    candles = make_candles(300)
    det = ReversalStrategyDetector()
    arr = det.signal_array(candles, {})
    assert len(arr) == len(candles)
    assert all(s in (-1, 0, 1) for s in arr)


# ═══════════════════════════════════════════════════════════════════════════════
#  端到端回测
# ═══════════════════════════════════════════════════════════════════════════════

def test_strategy_backtest_runs():
    """端到端回测: 不崩溃 + 产出有效报告。"""
    candles = make_candles(300)
    tester = StrategyBacktester(AccountConfig(initial_capital=1000.0))
    report = tester.run(candles, "SYN", "1day")

    assert report.symbol == "SYN"
    assert report.timeframe == "1day"
    assert report.total_trades >= 0
    # 回测报告的值应该有效
    assert isinstance(report.total_return_pct, float)
    assert isinstance(report.max_drawdown_pct, float)
    assert len(report.equity_curve) == len(candles)


def test_strategy_backtest_convenience():
    """便捷入口 run_backtest 可正常调用。"""
    candles = make_candles(300)
    report = run_backtest(candles, "SYN", "1day")
    assert report.symbol == "SYN"
    assert report.total_trades >= 0


def test_regime_specific_performance():
    """按 Regime 分组统计正常。"""
    candles = make_candles(300)
    report = run_backtest(candles, "SYN", "1day")
    # by_regime 是 dict[int, dict]
    for regime_id, stats in report.by_regime.items():
        assert 0 <= regime_id <= 5
        assert "trades" in stats
        assert "win_rate" in stats
        assert "avg_pnl_pct" in stats


def test_strategy_specific_performance():
    """按策略分组统计正常。"""
    candles = make_candles(300)
    report = run_backtest(candles, "SYN", "1day")
    for stype in ["trend", "momentum", "reversal"]:
        if stype in report.by_strategy:
            stats = report.by_strategy[stype]
            assert "trades" in stats
            assert "win_rate" in stats
