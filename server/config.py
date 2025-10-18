"""Configuration service propagating database changes to active worlds."""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Awaitable, Callable, Dict, Optional

from app.core.events import CONFIG_CHANNEL, config_pubsub


class ConfigService:
    """Listen for gameplay configuration changes and broadcast updates."""

    def __init__(
        self,
        *,
        fetch_config: Callable[[], Awaitable[Dict[str, Any]]],
        apply_config: Callable[[Dict[str, Any]], Awaitable[None]],
        broadcast: Callable[[Dict[str, Any]], Awaitable[None]],
    ) -> None:
        self._fetch_config = fetch_config
        self._apply_config = apply_config
        self._broadcast = broadcast
        self._task: Optional[asyncio.Task[None]] = None
        self._current: Dict[str, Any] = {}

    async def start(self) -> None:
        self._current = await self._fetch_config()
        await self._apply_config(self._current)
        self._task = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _listen(self) -> None:
        async for payload in config_pubsub.iterator(CONFIG_CHANNEL):
            if isinstance(payload, dict):
                self._current = payload
                await self._apply_config(payload)
                await self._broadcast(payload)

    async def refresh(self) -> Dict[str, Any]:
        self._current = await self._fetch_config()
        await self._apply_config(self._current)
        return dict(self._current)

    def snapshot(self) -> Dict[str, Any]:
        return dict(self._current)


async def load_config_from_database(fetch_sync: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fetch_sync)
