from typing import Any

from app.core.signal_registry import SignalHit, SignalRegistry


class RsiExtremeSignal:
    name = "rsi_extreme"
    display_name = "RSI 极值"
    category = "indicator"
    description = "识别 RSI 超买/超卖后的反向交易机会。"
    logic = "取最近 15 根收盘价近似计算 RSI；RSI <= 30 视为超卖做多信号，RSI >= 70 视为超买做空信号。"
    direction_support = ["long", "short"]
    params_schema = {"period": 14, "oversold": 30, "overbought": 70}

    def detect(self, candles: list[dict[str, Any]], timeframe: str) -> list[SignalHit]:
        if len(candles) < 15:
            return []
        closes = [float(c["close"]) for c in candles[-15:]]
        gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
        losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
        avg_gain = sum(gains) / len(gains)
        avg_loss = sum(losses) / len(losses)
        rsi = 100.0 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
        last = candles[-1]
        if rsi <= 30:
            direction = "long"
        elif rsi >= 70:
            direction = "short"
        else:
            return []
        return [SignalHit(self.name, direction, timeframe, 0.65, int(last["time"]), float(last["close"]), {"rsi": rsi})]


def register_indicator_signals(registry: SignalRegistry) -> None:
    registry.register(RsiExtremeSignal())
