"""World simulation, tick loop and persistence helpers."""
from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple
from uuid import uuid4

from .player import Player


Vector = Tuple[float, float]


@dataclass
class Cell:
    """Represents a cell controlled by a player."""

    player_id: str
    position: Vector
    radius: float
    velocity: Vector = (0.0, 0.0)

    def area(self) -> float:
        return math.pi * self.radius ** 2

    def to_dict(self) -> dict:
        return {
            "player_id": self.player_id,
            "position": self.position,
            "radius": self.radius,
        }


@dataclass
class Food:
    """Consumable that increases a player's mass."""

    id: str
    position: Vector
    value: float

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "position": self.position,
            "value": self.value,
        }


@dataclass
class WorldConfig:
    name: str
    width: float = 1000.0
    height: float = 1000.0
    tick_rate: float = 30.0
    food_count: int = 200
    snapshot_interval: float = 10.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "tick_rate": self.tick_rate,
            "food_count": self.food_count,
            "snapshot_interval": self.snapshot_interval,
        }


@dataclass
class WorldState:
    """Mutable state for a single world instance."""

    config: WorldConfig
    players: Dict[str, Player] = field(default_factory=dict)
    cells: Dict[str, Cell] = field(default_factory=dict)
    foods: Dict[str, Food] = field(default_factory=dict)
    last_update: float = field(default_factory=time.monotonic)
    targets: Dict[str, Vector] = field(default_factory=dict)

    def add_player(self, player: Player) -> Cell:
        spawn_position = self._find_spawn_position()
        cell = Cell(player_id=player.id, position=spawn_position, radius=25.0)
        self.players[player.id] = player
        self.cells[player.id] = cell
        self.targets[player.id] = spawn_position
        return cell

    def remove_player(self, player_id: str) -> None:
        self.players.pop(player_id, None)
        self.cells.pop(player_id, None)
        self.targets.pop(player_id, None)

    def set_target(self, player_id: str, target: Vector) -> None:
        if player_id in self.targets:
            tx = max(0.0, min(self.config.width, target[0]))
            ty = max(0.0, min(self.config.height, target[1]))
            self.targets[player_id] = (tx, ty)

    def _find_spawn_position(self) -> Vector:
        return (
            float(uuid4().int % int(self.config.width)),
            float(uuid4().int % int(self.config.height)),
        )

    def populate_food(self) -> None:
        target_food_count = max(0, int(self.config.food_count))

        while len(self.foods) > target_food_count:
            self.foods.popitem()

        while len(self.foods) < target_food_count:
            food_id = uuid4().hex
            position = (
                float(uuid4().int % int(self.config.width)),
                float(uuid4().int % int(self.config.height)),
            )
            self.foods[food_id] = Food(id=food_id, position=position, value=5.0)

    def tick(self, dt: float) -> None:
        for cell in self.cells.values():
            target = self.targets.get(cell.player_id, cell.position)
            dx = target[0] - cell.position[0]
            dy = target[1] - cell.position[1]
            distance = math.hypot(dx, dy)
            if distance > 1e-3:
                speed = max(20.0, 150.0 - cell.radius)
                vx = (dx / distance) * speed
                vy = (dy / distance) * speed
            else:
                vx = vy = 0.0
            cell.velocity = (vx, vy)
            cell.position = (
                max(0.0, min(self.config.width, cell.position[0] + vx * dt)),
                max(0.0, min(self.config.height, cell.position[1] + vy * dt)),
            )

        self._handle_food_collisions()
        self._handle_cell_collisions()

    def _handle_food_collisions(self) -> None:
        consumed: List[str] = []
        for food in self.foods.values():
            for cell in self.cells.values():
                if _collides(cell.position, cell.radius, food.position, 3.0):
                    consumed.append(food.id)
                    cell.radius += food.value * 0.1
                    player = self.players.get(cell.player_id)
                    if player:
                        player.score += food.value
                    break
        for food_id in consumed:
            self.foods.pop(food_id, None)
        self.populate_food()

    def _handle_cell_collisions(self) -> None:
        cells = list(self.cells.values())
        i = 0
        while i < len(cells):
            cell = cells[i]
            if cell.player_id not in self.cells:
                i += 1
                continue

            restart = False
            j = i + 1
            while j < len(cells):
                other = cells[j]
                if other.player_id not in self.cells:
                    j += 1
                    continue
                if cell.player_id == other.player_id:
                    j += 1
                    continue
                if _collides(cell.position, cell.radius, other.position, other.radius):
                    if cell.radius > other.radius * 1.1:
                        self._absorb(cell, other)
                    elif other.radius > cell.radius * 1.1:
                        self._absorb(other, cell)
                    else:
                        j += 1
                        continue

                    cells = list(self.cells.values())
                    restart = True
                    break
                j += 1

            if restart:
                i = 0
                continue

            i += 1

    def _absorb(self, winner: Cell, loser: Cell) -> None:
        new_area = winner.area() + loser.area() * 0.8
        winner.radius = math.sqrt(new_area / math.pi)
        self.remove_player(loser.player_id)

    def snapshot(self) -> dict:
        return {
            "config": self.config.to_dict(),
            "players": [player.to_dict() for player in self.players.values()],
            "cells": [cell.to_dict() for cell in self.cells.values()],
            "foods": [food.to_dict() for food in self.foods.values()],
        }


def _collides(pos_a: Vector, radius_a: float, pos_b: Vector, radius_b: float) -> bool:
    return math.hypot(pos_a[0] - pos_b[0], pos_a[1] - pos_b[1]) <= radius_a + radius_b


class WorldSnapshotRepository:
    """Persist world snapshots to disk."""

    def __init__(self, directory: str) -> None:
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)

    async def save_snapshot(self, world_id: str, snapshot: dict) -> None:
        path = self._directory / f"{world_id}.json"
        loop = asyncio.get_running_loop()
        data = json.dumps(snapshot)
        await loop.run_in_executor(None, path.write_text, data)


@dataclass
class WorldContext:
    state: WorldState
    listeners: List[asyncio.Queue[dict]]
    task: Optional[asyncio.Task] = None
    last_snapshot: float = field(default_factory=time.monotonic)


DisposeFunc = Callable[[], Awaitable[None]]

class WorldManager:
    """High level orchestration for world instances."""

    def __init__(self, snapshot_repo: WorldSnapshotRepository, *, default_tick_rate: float = 30.0):
        self._worlds: Dict[str, WorldContext] = {}
        self._snapshot_repo = snapshot_repo
        self._lock = asyncio.Lock()
        self._config_defaults: Dict[str, float] = {
            "width": 1000.0,
            "height": 1000.0,
            "tick_rate": default_tick_rate,
            "food_count": 200,
            "snapshot_interval": 10.0,
        }

    async def list_worlds(self) -> List[dict]:
        async with self._lock:
            return [
                {
                    "id": world_id,
                    "name": ctx.state.config.name,
                    "players": len(ctx.state.players),
                }
                for world_id, ctx in self._worlds.items()
            ]

    async def create_world(self, name: str) -> dict:
        async with self._lock:
            world_id = uuid4().hex
            defaults = self._config_defaults
            config = WorldConfig(
                name=name,
                width=float(defaults["width"]),
                height=float(defaults["height"]),
                tick_rate=float(defaults["tick_rate"]),
                food_count=int(defaults["food_count"]),
                snapshot_interval=float(defaults["snapshot_interval"]),
            )
            state = WorldState(config=config)
            state.populate_food()
            ctx = WorldContext(state=state, listeners=[])
            ctx.task = asyncio.create_task(self._run_world(world_id, ctx))
            self._worlds[world_id] = ctx
            return {"id": world_id, "name": name}

    async def get_world(self, world_id: str) -> Optional[WorldState]:
        async with self._lock:
            ctx = self._worlds.get(world_id)
            return ctx.state if ctx else None

    async def remove_player(self, world_id: str, player_id: str) -> None:
        state = await self.get_world(world_id)
        if state:
            state.remove_player(player_id)

    async def add_player(self, world_id: str, player: Player) -> Optional[Cell]:
        state = await self.get_world(world_id)
        if not state:
            return None
        return state.add_player(player)

    async def set_target(self, world_id: str, player_id: str, target: Vector) -> None:
        state = await self.get_world(world_id)
        if state:
            state.set_target(player_id, target)

    async def subscribe(self, world_id: str) -> "WorldSubscription":
        async with self._lock:
            ctx = self._worlds.get(world_id)
            if not ctx:
                raise KeyError(world_id)
            queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1)
            ctx.listeners.append(queue)

        async def remove_listener() -> None:
            async with self._lock:
                ctx = self._worlds.get(world_id)
                if ctx and queue in ctx.listeners:
                    ctx.listeners.remove(queue)

        return WorldSubscription(queue=queue, dispose=remove_listener)

    async def _run_world(self, world_id: str, ctx: WorldContext) -> None:
        state = ctx.state
        while True:
            now = time.monotonic()
            dt = now - state.last_update
            state.last_update = now
            state.tick(dt)
            snapshot = state.snapshot()
            listeners = list(ctx.listeners)
            for queue in listeners:
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                await queue.put(snapshot)
            if now - ctx.last_snapshot >= state.config.snapshot_interval:
                await self._snapshot_repo.save_snapshot(world_id, snapshot)
                ctx.last_snapshot = now
            tick_rate = max(1e-3, state.config.tick_rate)
            tick_interval = 1.0 / tick_rate
            await asyncio.sleep(tick_interval)

    async def update_config(self, values: Dict[str, Any]) -> None:
        async with self._lock:
            self._config_defaults.update({k: float(v) for k, v in values.items() if k in {"width", "height", "tick_rate", "snapshot_interval"}})
            if "food_count" in values:
                self._config_defaults["food_count"] = float(values["food_count"])
            for ctx in self._worlds.values():
                state = ctx.state
                state.config.width = float(self._config_defaults["width"])
                state.config.height = float(self._config_defaults["height"])
                state.config.tick_rate = float(self._config_defaults["tick_rate"])
                state.config.snapshot_interval = float(self._config_defaults["snapshot_interval"])
                state.config.food_count = int(self._config_defaults["food_count"])
                state.populate_food()


@dataclass
class WorldSubscription:
    queue: asyncio.Queue[dict]
    dispose: DisposeFunc

    def __aiter__(self) -> Iterable[dict]:
        return self

    async def __anext__(self) -> dict:
        try:
            return await self.queue.get()
        except asyncio.CancelledError as exc:  # pragma: no cover - sanity guard
            raise StopAsyncIteration from exc

    async def close(self) -> None:
        await self.dispose()
