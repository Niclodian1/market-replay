"""回测指标信号探测器 —— 把 backtest_engine 原先硬编码的六个 `_signals_*`
包装成统一的 SignalDetector。

这是 strategy-spine 的"信号层"，让回测引擎与策略实验台共享同一份信号定义。
每个探测器都:

- `signal_array()` 产出逐根点信号(1/-1/0) —— 回测引擎消费;
- `detect()` 把同一数组转成离散 SignalHit 事件 —— 策略探测器消费。

参数解析与旧 `_make_signals` 完全一致,行为由 golden 测试锁定。
"""
from typing import Any

from app.core.signal_registry import SignalHit, SignalRegistry
from app.core.divergence_detectors import macd_extreme_divergence_array
from app.core.signals import primitives
from app.core.signals.evidence import (
    build_signal_payload,
    ma_breakout_evidence,
)


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
    strategy_type = "indicator"       # 原始指标信号，不受 regime 过滤
    valid_regimes: set[int] = set()   # 空集 = 不限

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

    def detect(self, candles, timeframe):
        period = int(self.params_schema["period"])
        arr = self.signal_array(candles, self.params_schema)
        hits: list[SignalHit] = []
        for i, signal in enumerate(arr):
            if signal == 0:
                continue
            direction = "long" if signal == 1 else "short"
            evidence = ma_breakout_evidence(candles, i, period, direction)
            payload = build_signal_payload(
                signal_type=self.name,
                direction=direction,
                state="confirmed",
                candle=candles[i],
                timeframe=timeframe,
                evidence=evidence,
                parameters={"period": period, "volume_windows": [5, 10], "atr_period": 14},
            )
            hits.append(SignalHit(
                name=self.name,
                direction=direction,
                timeframe=timeframe,
                score=0.6,
                fired_at_ts=int(candles[i]["time"]),
                fired_price=float(candles[i]["close"]),
                payload=payload,
            ))
        return hits


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
    display_name = "MACD 柱极值背离"
    category = "indicator"
    description = "当前动能段极值相对前一同向段极值减弱（柱极值背离）。"
    logic = "柱极值动能减弱 → 底背离做多 / 顶背离做空。"
    params_schema = {}

    def signal_array(self, candles, params):
        return macd_extreme_divergence_array(candles)


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
