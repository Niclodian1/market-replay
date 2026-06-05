from typing import Any

from app.core.signal_registry import SignalHit, SignalRegistry


class EmaTrendSignal:
    name = "ema_trend"
    display_name = "均线趋势"
    category = "structure"
    description = "用快慢均线位置判断当前趋势方向。"
    logic = "取最近 50 根 K 线，计算近 20 根均价与近 50 根均价；快线高于慢线且差距超过约 0.2% 判定做多趋势，反之做空。"
    direction_support = ["long", "short"]
    params_schema = {"fast_window": 20, "slow_window": 50}

    def detect(self, candles: list[dict[str, Any]], timeframe: str) -> list[SignalHit]:
        if len(candles) < 50:
            return []
        closes = [float(c["close"]) for c in candles[-50:]]
        ema_fast = sum(closes[-20:]) / 20
        ema_slow = sum(closes) / 50
        last = candles[-1]
        spread = abs(ema_fast - ema_slow) / max(float(last["close"]), 1e-9)
        if spread < 0.002:
            return []
        direction = "long" if ema_fast > ema_slow else "short"
        return [SignalHit(self.name, direction, timeframe, 0.6, int(last["time"]), float(last["close"]), {"ema_fast": ema_fast, "ema_slow": ema_slow})]


def register_structure_signals(registry: SignalRegistry) -> None:
    registry.register(EmaTrendSignal())
