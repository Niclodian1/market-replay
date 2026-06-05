from typing import Any

from app.core.signal_registry import SignalHit, SignalRegistry


class PinbarSignal:
    name = "pinbar"
    display_name = "Pinbar 反转"
    category = "pattern"
    description = "识别长影线 Pinbar，捕捉潜在反转。"
    logic = "检查最新 K 线实体和上下影线；下影线 >= 实体 2.5 倍且上影线较短为做多 Pinbar，反之为做空 Pinbar。"
    direction_support = ["long", "short"]
    params_schema = {"wick_body_ratio": 2.5}

    def detect(self, candles: list[dict[str, Any]], timeframe: str) -> list[SignalHit]:
        if not candles:
            return []
        candle = candles[-1]
        open_price = float(candle["open"])
        close = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])
        body = max(abs(close - open_price), 1e-9)
        upper = high - max(open_price, close)
        lower = min(open_price, close) - low
        if lower >= body * 2.5 and upper <= body * 1.2:
            direction = "long"
        elif upper >= body * 2.5 and lower <= body * 1.2:
            direction = "short"
        else:
            return []
        return [SignalHit(self.name, direction, timeframe, 0.7, int(candle["time"]), close, {"body": body, "upper": upper, "lower": lower})]


def register_pattern_signals(registry: SignalRegistry) -> None:
    registry.register(PinbarSignal())
