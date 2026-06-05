from app.core.event_bus import Event, event_bus
from app.db.session import AsyncSessionLocal
from app.tasks.edge_discovery import run_edge_discovery_for_pair


async def handle_candle_closed(event: Event) -> None:
    symbol = str(event.payload.get("symbol", "")).upper()
    timeframe = str(event.payload.get("timeframe", ""))
    if not symbol or not timeframe:
        return
    async with AsyncSessionLocal() as db:
        await run_edge_discovery_for_pair(db, symbol, timeframe)


def register_opportunity_consumer() -> None:
    event_bus.subscribe("candle.closed", handle_candle_closed)
