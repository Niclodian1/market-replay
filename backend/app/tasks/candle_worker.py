from app.core.candle_store import register_on_new_candle_callback
from app.core.event_bus import event_bus


async def emit_candle_closed(symbol: str, timeframe: str, ts: int) -> None:
    await event_bus.emit("candle.closed", symbol=symbol.upper(), timeframe=timeframe, ts=ts)


def register_candle_worker_hooks() -> None:
    register_on_new_candle_callback(emit_candle_closed)
