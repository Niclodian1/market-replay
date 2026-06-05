"""回测引擎 golden（特征）测试。

目的:在 strategy-spine 重构期间锁住 `backtest_engine` 的现有行为。
Phase 1 会把六个 `_signals_*` 函数包成 SignalDetector 并让 `_make_signals`
改走信号注册表 —— 这些测试保证改完之后**信号数组与回测结果一字不差**。

基准值由重构前的引擎一次性捕获(见各常量旁注释)。若某项确实需要变更,
请有意识地更新对应常量,而不是无脑改数字。
"""
from app.core import backtest_engine as be
from tests.golden_helpers import digest, make_candles


# ── 基准:六个原始信号函数的输出摘要(Phase 1 detector 必须复现)──────────────
SIG_DIGESTS = {
    "ma_cross": "3f2f5cb6981319d0",
    "rsi": "f2208f8f95a93bb2",
    "macd": "a4cab8a518586a65",
    "ma_breakout": "de4bc7bfb5bb878c",
    "bollinger": "e9e44cd80b72d379",
    "macd_divergence": "72fa535c5d8f4280",
}


def _closes():
    return [c["close"] for c in make_candles()]


def test_signal_ma_cross_stable():
    assert digest(be._signals_ma_cross(_closes(), 10, 30)) == SIG_DIGESTS["ma_cross"]


def test_signal_rsi_stable():
    assert digest(be._signals_rsi(_closes(), 30.0, 70.0)) == SIG_DIGESTS["rsi"]


def test_signal_macd_stable():
    assert digest(be._signals_macd(_closes())) == SIG_DIGESTS["macd"]


def test_signal_ma_breakout_stable():
    assert digest(be._signals_ma_breakout(_closes(), 20)) == SIG_DIGESTS["ma_breakout"]


def test_signal_bollinger_stable():
    assert digest(be._signals_bollinger(_closes(), 20, 2.0)) == SIG_DIGESTS["bollinger"]


def test_signal_macd_divergence_stable():
    assert digest(be._signals_macd_divergence(_closes(), 5)) == SIG_DIGESTS["macd_divergence"]


# ── 基准:端到端 run_backtest(单信号)────────────────────────────────────────
MA_CROSS_RESULT = {
    "total_return_pct": 13.6212,
    "total_trades": 4,
    "win_rate_pct": 50.0,
    "max_drawdown_pct": 11.4314,
    "sharpe_ratio": 1.7381,
    "annualized_return_pct": 10682.8175,
    "buy_hold_return_pct": 25.6923,
    "trades_digest": "941956c39d270f0d",
    "equity_digest": "cb76065b484d7dd5",
}


def test_run_backtest_ma_cross_golden():
    candles = make_candles()
    r = be.run_backtest(
        candles, "SYN", "1h", "2020", "2021",
        [{"strategy": "ma_cross", "params": {"fast_period": 10, "slow_period": 30}}],
    )
    assert r.total_return_pct == MA_CROSS_RESULT["total_return_pct"]
    assert r.total_trades == MA_CROSS_RESULT["total_trades"]
    assert r.win_rate_pct == MA_CROSS_RESULT["win_rate_pct"]
    assert r.max_drawdown_pct == MA_CROSS_RESULT["max_drawdown_pct"]
    assert r.sharpe_ratio == MA_CROSS_RESULT["sharpe_ratio"]
    assert r.annualized_return_pct == MA_CROSS_RESULT["annualized_return_pct"]
    assert r.buy_hold_return_pct == MA_CROSS_RESULT["buy_hold_return_pct"]
    assert digest(r.trades) == MA_CROSS_RESULT["trades_digest"]
    assert digest(r.equity_curve) == MA_CROSS_RESULT["equity_digest"]


# ── 基准:多信号 AND 组合路径 ─────────────────────────────────────────────────
COMBO_RESULT = {
    "total_return_pct": 18.469,
    "total_trades": 2,
    "win_rate_pct": 100.0,
    "trades_digest": "7b71e492d213a42d",
}


def test_run_backtest_combo_and_golden():
    candles = make_candles()
    r = be.run_backtest(
        candles, "SYN", "1h", "2020", "2021",
        [
            {"strategy": "ma_cross", "params": {"fast_period": 10, "slow_period": 30}},
            {"strategy": "rsi", "params": {"oversold": 30, "overbought": 70}},
        ],
        combine_mode="and",
    )
    assert r.total_return_pct == COMBO_RESULT["total_return_pct"]
    assert r.total_trades == COMBO_RESULT["total_trades"]
    assert r.win_rate_pct == COMBO_RESULT["win_rate_pct"]
    assert digest(r.trades) == COMBO_RESULT["trades_digest"]
