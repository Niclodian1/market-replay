import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

EventHandler = Callable[["Event"], Awaitable[None]]


@dataclass(frozen=True)
class Event:
    name: str
    payload: dict[str, Any]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        if handler not in self._handlers[event_name]:
            self._handlers[event_name].append(handler)

    async def emit(self, event_name: str, **payload: Any) -> None:
        event = Event(event_name, payload)
        handlers = list(self._handlers.get(event_name, []))
        if handlers:
            await asyncio.gather(*(handler(event) for handler in handlers))


event_bus = EventBus()
