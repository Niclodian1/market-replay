"""回测指标信号探测器 —— 把 backtest_engine 原先硬编码的六个 `_signals_*`
包装成统一的 SignalDetector。

这是 strategy-spine 的"信号层":回测引擎(run_backtest)与 edge 流水线
(SignalDetector.detect)从此共享同一份信号定义。每个探测器都:

- `signal_array()` 产出逐根点信号(1/-1/0) —— 回测引擎消费;
- `detect()` 把同一数组转成离散 SignalHit 事件 —— edge 流水线消费。

参数解析与旧 `_make_signals` 完全一致,行为由 golden 测试锁定。
"""
from typing import Any

from app.core.signal_registry import SignalHit, SignalRegistry
from app.core.signals import primitives


def _array_to_hits(name: str, arr: list[int], candles: list[dict[str, Any]], timeframe: str,
                   score: float = 0.6) -> list[SignalHit]:
    hits: list[SignalHit] = []
    for i, s in enumerate(arr):
        if s == 0:
            continue
        c = candles[i]
        hits.append(SignalHit(
            name=name,
            direction="long" if s == 1 else "short",
            timeframe=timeframe,
            score=score,
            fired_at_ts=int(c["time"]),
            fired_price=float(c["close"]),
            payload={},
        ))
    return hits


class _Base:
    """共用样板:由 signal_array() 推导 detect()。子类实现 signal_array()。"""
    name: str
    display_name: str
    category: str
    description: str
    logic: str
    direction_support = ["long", "short"]
    params_schema: dict[str, Any] = {}

    def signal_array(self, candles: list[dict[str, Any]], params: dict[str, Any]) -> list[int]:
        raise NotImplementedError

    def detect(self, candles: list[dict[str, Any]], timeframe: str) -> list[SignalHit]:
        return _array_to_hits(self.name, self.signal_array(candles, {}), candles, timeframe)


class MaCrossSignal(_Base):
    name = "ma_cross"
    display_name = "均线金叉/死叉"
    category = "indicator"
    description = "快慢均线交叉。金叉做多,死叉做空。"
    logic = "fast SMA 上穿 slow SMA → 买;下穿 → 卖。"
    params_schema = {"fast_period": 10, "slow_period": 30}

    def signal_array(self, candles, params):
        closes = [c["close"] for c in candles]
        return primitives._signals_ma_cross(
            closes, int(params.get("fast_period", 10)), int(params.get("slow_period", 30)))


class RsiSignal(_Base):
    name = "rsi"
    display_name = "RSI 超买超卖"
    category = "indicator"
    description = "RSI 跌破超卖线做多,升破超买线做空。"
    logic = "RSI(14) 下穿 oversold → 买;上穿 overbought → 卖。"
    params_schema = {"oversold": 30, "overbought": 70}

    def signal_array(self, candles, params):
        closes = [c["close"] for c in candles]
        return primitives._signals_rsi(
            closes, float(params.get("oversold", 30.0)), float(params.get("overbought", 70.0)))


class MacdSignal(_Base):
    name = "macd"
    display_name = "MACD 交叉"
    category = "indicator"
    description = "MACD 线与信号线交叉。"
    logic = "MACD 上穿 signal → 买;下穿 → 卖。"
    params_schema = {}

    def signal_array(self, candles, params):
        closes = [c["close"] for c in candles]
        return primitives._signals_macd(closes)


class MaBreakoutSignal(_Base):
    name = "ma_breakout"
    display_name = "均线突破"
    category = "indicator"
    description = "收盘价向上/向下穿越单条均线。"
    logic = "close 上穿 SMA(period) → 买;下穿 → 卖。"
    params_schema = {"period": 20}

    def signal_array(self, candles, params):
        closes = [c["close"] for c in candles]
        return primitives._signals_ma_breakout(closes, int(params.get("period", 20)))


class BollingerSignal(_Base):
    name = "bollinger"
    display_name = "布林带反转"
    category = "indicator"
    description = "跌破下轨做多,升破上轨做空。"
    logic = "close 下穿 lower band → 买;上穿 upper band → 卖。"
    params_schema = {"period": 20, "num_std": 2.0}

    def signal_array(self, candles, params):
        closes = [c["close"] for c in candles]
        return primitives._signals_bollinger(
            closes, int(params.get("period", 20)), float(params.get("num_std", 2.0)))


class MacdDivergenceSignal(_Base):
    name = "macd_divergence"
    display_name = "MACD 背离"
    category = "indicator"
    description = "价格与 MACD 柱状图背离。"
    logic = "价格创新低而 MACD 柱抬高(底背离)→ 买;反之 → 卖。"
    params_schema = {"lookback": 5}

    def signal_array(self, candles, params):
        closes = [c["close"] for c in candles]
        return primitives._signals_macd_divergence(closes, int(params.get("lookback", 5)))


_DETECTORS = [
    MaCrossSignal(), RsiSignal(), MacdSignal(),
    MaBreakoutSignal(), BollingerSignal(), MacdDivergenceSignal(),
]


def build_backtest_registry() -> SignalRegistry:
    registry = SignalRegistry()
    for det in _DETECTORS:
        registry.register(det)
    return registry


backtest_registry = build_backtest_registry()


def get_backtest_signal(name: str):
    """按策略 ID 取回测信号探测器;未知 ID 抛 KeyError。"""
    return backtest_registry.get(name)
