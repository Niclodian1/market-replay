"""策略 C golden 测试 —— 锁住从脚本忠实移植(方案 B)的执行模型行为。

fixture 为 600 根真实 BTC 日线(tests/fixtures/strategy_c_candles.json)。
基准由移植版捕获,且移植版已与原脚本生成的真实 JSON 逐笔对拍完全一致。
"""
import json
from pathlib import Path

from app.core.strategies.strategy_c import run_strategy_c, MultiMaCrossUpSignal
from tests.golden_helpers import digest

FIXTURE = Path(__file__).parent / "fixtures" / "strategy_c_candles.json"


def _candles():
    return json.loads(FIXTURE.read_text())


EXPECT = {
    2: dict(n_trades=6, total_return=29.5308, win_rate=50.0, trades_digest="55caea9c440f3563"),
    3: dict(n_trades=1, total_return=-6.3237, win_rate=0.0, trades_digest="5ebc8343e829f073"),
}


def test_run_strategy_c_golden():
    res = run_strategy_c(_candles())
    assert res is not None
    assert res["best_k"] == 2
    for k, exp in EXPECT.items():
        block = res["variants"][k]
        assert block["stats"]["n_trades"] == exp["n_trades"], f"C-x{k} 成交数"
        assert block["stats"]["total_return"] == exp["total_return"], f"C-x{k} 收益"
        assert block["stats"]["win_rate"] == exp["win_rate"], f"C-x{k} 胜率"
        assert digest(block["trades"]) == exp["trades_digest"], f"C-x{k} 逐笔"


def test_entry_detector_matches_executor_entries():
    """信号层(detector)入场计数应等于执行层(run_variant)成交数 —— 脊柱两层一致。"""
    candles = _candles()
    res = run_strategy_c(candles)
    det = MultiMaCrossUpSignal()
    for k in EXPECT:
        arr = det.signal_array(candles, {"k": k})
        assert sum(arr) == res["variants"][k]["stats"]["n_trades"], f"k={k} 入场数≠成交数"
