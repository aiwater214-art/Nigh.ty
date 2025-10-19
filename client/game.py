"""Client side gameplay loop implemented with pygame."""
from __future__ import annotations

import asyncio
import json
import urllib.parse
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Dict, Optional

try:
    import pygame
except ModuleNotFoundError as exc:  # pragma: no cover - pygame optional in CI
    raise RuntimeError(
        "pygame is required for the interactive client. Install it via 'pip install pygame'."
    ) from exc

import websockets


@dataclass
class Entity:
    id: str
    position: tuple[float, float]
    radius: float
    color: tuple[int, int, int] = (255, 255, 255)


@dataclass
class WorldView:
    width: float
    height: float
    tick_rate: float = 30.0
    food_count: int = 200
    snapshot_interval: float = 10.0
    players: Dict[str, Entity] = field(default_factory=dict)
    foods: Dict[str, Entity] = field(default_factory=dict)

    def apply_config(self, config: dict) -> None:
        self.width = float(config.get("width", self.width))
        self.height = float(config.get("height", self.height))
        self.tick_rate = float(config.get("tick_rate", self.tick_rate))
        self.food_count = int(config.get("food_count", self.food_count))
        self.snapshot_interval = float(config.get("snapshot_interval", self.snapshot_interval))

    def update_from_snapshot(self, snapshot: dict) -> None:
        config = snapshot.get("config", {})
        if config:
            self.apply_config(config)

        self.players.clear()
        for player in snapshot.get("players", []):
            cell = next(
                (c for c in snapshot.get("cells", []) if c.get("player_id") == player["id"]),
                None,
            )
            if not cell:
                continue
            self.players[player["id"]] = Entity(
                id=player["id"],
                position=tuple(cell["position"]),
                radius=float(cell["radius"]),
                color=tuple(player.get("color", (200, 200, 255))),
            )

        self.foods.clear()
        for food in snapshot.get("foods", []):
            self.foods[food["id"]] = Entity(
                id=food["id"],
                position=tuple(food["position"]),
                radius=3.0,
                color=(80, 200, 120),
            )


class GameClient:
    """Handles websocket interaction and rendering."""

    def __init__(self, base_ws_url: str, world_id: str, token: str, player_name: str, *, initial_config: Optional[dict] = None) -> None:
        self._base_ws_url = base_ws_url.rstrip("/")
        self._world_id = world_id
        self._token = token
        self._player_name = player_name
        self._world = WorldView(width=1000.0, height=1000.0)
        if initial_config:
            self._world.apply_config(initial_config)
        self._running = True
        self._player_id: Optional[str] = None

    async def run(self) -> None:
        query_params: list[tuple[str, str]] = []
        if self._token is not None:
            query_params.append(("token", self._token))
        if self._player_name is not None:
            query_params.append(("player_name", self._player_name))

        query_string = urllib.parse.urlencode(query_params)
        ws_url = f"{self._base_ws_url}/ws/world/{self._world_id}"
        if query_string:
            ws_url = f"{ws_url}?{query_string}"
        async with websockets.connect(ws_url) as websocket:
            receiver = asyncio.create_task(self._receiver(websocket))
            try:
                await self._render_loop(websocket)
            finally:
                self._running = False
                receiver.cancel()
                with suppress(asyncio.CancelledError):
                    await receiver

    async def _receiver(self, websocket: websockets.WebSocketClientProtocol) -> None:
        async for message in websocket:
            data = json.loads(message)
            msg_type = data.get("type")
            if msg_type == "joined":
                player = data.get("player", {})
                self._player_id = player.get("id")
                config = data.get("config")
                if isinstance(config, dict):
                    self._world.apply_config(config)
            elif msg_type == "world":
                state = data.get("state", {})
                self._world.update_from_snapshot(state)
            elif msg_type == "config_update":
                config = data.get("config")
                if isinstance(config, dict):
                    self._world.apply_config(config)

    async def _render_loop(self, websocket: websockets.WebSocketClientProtocol) -> None:
        pygame.init()
        window_size = (800, 600)
        screen = pygame.display.set_mode(window_size)
        pygame.display.set_caption(f"World {self._world_id}")
        clock = pygame.time.Clock()

        while self._running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self._running = False

            screen.fill((15, 15, 26))
            self._draw_world(screen, window_size)
            pygame.display.flip()

            if pygame.mouse.get_focused() and self._player_id:
                mx, my = pygame.mouse.get_pos()
                target = self._screen_to_world(mx, my, window_size)
                payload = json.dumps({"type": "set_target", "target": [target[0], target[1]]})
                await websocket.send(payload)

            clock.tick(60)
            await asyncio.sleep(0)

        pygame.quit()

    def _draw_world(self, screen: pygame.Surface, window_size: tuple[int, int]) -> None:
        for food in self._world.foods.values():
            pygame.draw.circle(
                screen,
                food.color,
                self._world_to_screen(food.position, window_size),
                max(1, int(food.radius)),
            )

        for player in self._world.players.values():
            pygame.draw.circle(
                screen,
                player.color,
                self._world_to_screen(player.position, window_size),
                max(5, int(player.radius)),
            )

    def _world_to_screen(self, position: tuple[float, float], window_size: tuple[int, int]) -> tuple[int, int]:
        scale_x = window_size[0] / self._world.width
        scale_y = window_size[1] / self._world.height
        return int(position[0] * scale_x), int(position[1] * scale_y)

    def _screen_to_world(self, x: int, y: int, window_size: tuple[int, int]) -> tuple[float, float]:
        scale_x = self._world.width / window_size[0]
        scale_y = self._world.height / window_size[1]
        return x * scale_x, y * scale_y
