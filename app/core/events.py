"""Application-wide pub/sub primitives used for configuration updates."""
from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Tuple


class LocalPubSub:
    """A lightweight, in-memory pub/sub hub with asyncio-friendly APIs.

    The hub keeps track of subscribers per channel. Subscribers register from
    an asyncio event loop and receive updates via an ``asyncio.Queue``. The
    ``publish`` method can safely be called from any thread and will dispatch
    the payload to all subscribers using ``loop.call_soon_threadsafe``.
    """

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Tuple[asyncio.Queue[Any], asyncio.AbstractEventLoop]]] = {}
        self._lock = threading.Lock()

    @asynccontextmanager
    async def subscribe(self, channel: str) -> AsyncIterator[asyncio.Queue[Any]]:
        """Subscribe to a channel and yield the queue delivering messages.

        The queue is automatically unregistered when the context manager exits.
        """

        queue: asyncio.Queue[Any] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        with self._lock:
            self._subscribers.setdefault(channel, []).append((queue, loop))
        try:
            yield queue
        finally:
            with self._lock:
                subscribers = self._subscribers.get(channel, [])
                self._subscribers[channel] = [
                    (q, l) for (q, l) in subscribers if q is not queue
                ]

    async def iterator(self, channel: str) -> AsyncIterator[Any]:
        """Convenience wrapper yielding messages from a channel."""

        async with self.subscribe(channel) as queue:
            while True:
                yield await queue.get()

    def publish(self, channel: str, message: Any) -> None:
        """Publish *message* to *channel*.

        The method is thread-safe and may be called from synchronous contexts.
        """

        with self._lock:
            subscribers = list(self._subscribers.get(channel, []))
        for queue, loop in subscribers:
            loop.call_soon_threadsafe(queue.put_nowait, message)


# Global pub/sub instance and configuration channel name used throughout the app.
config_pubsub = LocalPubSub()
CONFIG_CHANNEL = "config:gameplay"

# Dedicated pub/sub instance for statistics updates pushed to the dashboard UI.
stats_pubsub = LocalPubSub()
STATS_CHANNEL = "stats:updates"

