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
    owner_id: Optional[str] = None


@dataclass
class WorldView:
    width: float
    height: float
    tick_rate: float = 30.0
    food_count: int = 200
    snapshot_interval: float = 10.0
    players: Dict[str, Entity] = field(default_factory=dict)
    cells: Dict[str, Entity] = field(default_factory=dict)
    player_cells: Dict[str, list[str]] = field(default_factory=dict)
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
        player_map: Dict[str, dict] = {}
        for player in snapshot.get("players", []):
            player_map[player["id"]] = player
            self.players[player["id"]] = Entity(
                id=player["id"],
                position=(0.0, 0.0),
                radius=0.0,
                color=tuple(player.get("color", (200, 200, 255))),
            )

        self.cells.clear()
        self.player_cells.clear()
        for cell in snapshot.get("cells", []):
            owner_id = cell.get("player_id")
            player_meta = player_map.get(owner_id, {})
            entity = Entity(
                id=cell["id"],
                position=tuple(cell["position"]),
                radius=float(cell["radius"]),
                color=tuple(player_meta.get("color", (200, 200, 255))),
                owner_id=owner_id,
            )
            self.cells[entity.id] = entity
            if owner_id:
                self.player_cells.setdefault(owner_id, []).append(entity.id)

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
        self._eliminated = False

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
            elif msg_type == "eliminated":
                self._eliminated = True
                self._running = False
                return
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
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_SPACE and self._player_id:
                    await websocket.send(json.dumps({"type": "split"}))

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

    def was_eliminated(self) -> bool:
        return self._eliminated

    def _draw_world(self, screen: pygame.Surface, window_size: tuple[int, int]) -> None:
        for food in self._world.foods.values():
            pygame.draw.circle(
                screen,
                food.color,
                self._world_to_screen(food.position, window_size),
                max(1, int(food.radius)),
            )

        my_player_id = self._player_id
        for cell in self._world.cells.values():
            position = self._world_to_screen(cell.position, window_size)
            radius = max(5, int(cell.radius))
            pygame.draw.circle(screen, cell.color, position, radius)
            if cell.owner_id == my_player_id:
                pygame.draw.circle(screen, (255, 255, 255), position, radius, 2)

    def _world_to_screen(self, position: tuple[float, float], window_size: tuple[int, int]) -> tuple[int, int]:
        scale_x = window_size[0] / self._world.width
        scale_y = window_size[1] / self._world.height
        return int(position[0] * scale_x), int(position[1] * scale_y)

    def _screen_to_world(self, x: int, y: int, window_size: tuple[int, int]) -> tuple[float, float]:
        scale_x = self._world.width / window_size[0]
        scale_y = self._world.height / window_size[1]
        return x * scale_x, y * scale_y
