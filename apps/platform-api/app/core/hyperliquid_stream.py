"""One shared Hyperliquid upstream websocket fan-out for local clients."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress

from websockets.asyncio.client import connect

from app.providers.hyperliquid import WS_URL, get_hyperliquid_provider, to_canonical_symbol


logger = logging.getLogger(__name__)


class HyperliquidStream:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._latest: dict[str, float] = {}

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="hyperliquid-all-mids")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=8)
        self._subscribers.add(queue)
        if self._latest:
            queue.put_nowait({"type": "allMids", "timestamp": int(time.time() * 1000), "mids": self._latest})
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def _publish(self, payload: dict) -> None:
        for queue in tuple(self._subscribers):
            if queue.full():
                with suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with suppress(asyncio.QueueFull):
                queue.put_nowait(payload)

    async def _all_mids_subscriptions(self) -> list[dict]:
        subscriptions = [{"type": "allMids"}]
        try:
            dex_names = await get_hyperliquid_provider().get_perp_dex_names()
            subscriptions.extend({"type": "allMids", "dex": dex} for dex in dex_names)
        except Exception as exc:
            logger.warning("Unable to load Hyperliquid HIP-3 DEX list: %s", exc)
        return subscriptions

    async def _run(self) -> None:
        backoff = 1
        while True:
            try:
                async with connect(WS_URL, ping_interval=20, ping_timeout=20, close_timeout=5) as websocket:
                    for subscription in await self._all_mids_subscriptions():
                        await websocket.send(json.dumps({
                            "method": "subscribe",
                            "subscription": subscription,
                        }))
                    backoff = 1
                    async for raw in websocket:
                        message = json.loads(raw)
                        if message.get("channel") != "allMids":
                            continue
                        mids = message.get("data", {}).get("mids", {})
                        normalized = {}
                        for coin, price in mids.items():
                            try:
                                normalized[to_canonical_symbol(coin)] = float(price)
                            except (TypeError, ValueError):
                                continue
                        if not normalized:
                            continue
                        self._latest.update(normalized)
                        self._publish({
                            "type": "allMids",
                            "timestamp": int(time.time() * 1000),
                            "mids": normalized,
                        })
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Hyperliquid websocket disconnected: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)


hyperliquid_stream = HyperliquidStream()
