"""World simulation, tick loop and persistence helpers."""
from __future__ import annotations

import asyncio
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional
from uuid import uuid4

from .physics import (
    BASE_TARGET_SPEED,
    BOOST_SPEED_MULTIPLIER,
    MASS_SPEED_EXPONENT,
    MAX_DELTA_TIME,
    MIN_TARGET_SPEED,
    CollisionEvent,
    PhysicsEngine,
    Vector,
)
from .player import Player


SPLIT_MIN_RADIUS = 30.0
SPLIT_COOLDOWN = 2.0
MERGE_DELAY = 3.0
MERGE_DISTANCE_FACTOR = 0.9
ABSORB_RATIO = 1.02


@dataclass
class Cell:
    """Represents a cell controlled by a player."""

    id: str
    player_id: str
    position: Vector
    radius: float
    velocity: Vector = (0.0, 0.0)
    merge_ready_at: float = 0.0

    def area(self) -> float:
        return math.pi * self.radius ** 2

    def to_dict(self) -> dict:
        return {
            "id": self.id,
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
    player_cells: Dict[str, List[str]] = field(default_factory=dict)
    foods: Dict[str, Food] = field(default_factory=dict)
    last_update: float = field(default_factory=time.monotonic)
    targets: Dict[str, Vector] = field(default_factory=dict)
    events: List[dict] = field(default_factory=list)
    split_cooldowns: Dict[str, float] = field(default_factory=dict)
    engine: PhysicsEngine = field(init=False)

    def __post_init__(self) -> None:
        self.engine = PhysicsEngine(self.config.width, self.config.height)

    def add_player(self, player: Player) -> Cell:
        spawn_position = self._find_spawn_position()
        # Use the player's id for the initial cell id so that a solo cell's
        # identifier is stable and corresponds to its owner. This simplifies
        # reasoning about ownership in clients and tests.
        cell = Cell(id=player.id, player_id=player.id, position=spawn_position, radius=25.0)
        self.players[player.id] = player
        self.cells[cell.id] = cell
        self.player_cells[player.id] = [cell.id]
        self.targets[player.id] = spawn_position
        self.split_cooldowns[player.id] = 0.0
        self.engine.add_cell(cell, owner_id=player.id)
        return cell

    def remove_player(self, player_id: str) -> None:
        for cell_id in list(self.player_cells.get(player_id, [])):
            self._remove_cell(cell_id)
        self.players.pop(player_id, None)
        self.player_cells.pop(player_id, None)
        self.targets.pop(player_id, None)
        self.split_cooldowns.pop(player_id, None)

    def set_target(self, player_id: str, target: Vector) -> None:
        if player_id in self.targets:
            tx = max(0.0, min(self.config.width, target[0]))
            ty = max(0.0, min(self.config.height, target[1]))
            clamped = (tx, ty)
            self.targets[player_id] = clamped
            for cell_id in self.player_cells.get(player_id, []):
                if cell_id in self.cells:
                    self.engine.set_cell_target(cell_id, clamped)

    def _remove_cell(self, cell_id: str) -> None:
        cell = self.cells.pop(cell_id, None)
        if not cell:
            return
        cells = self.player_cells.get(cell.player_id)
        if cells and cell_id in cells:
            cells.remove(cell_id)
            if not cells:
                self.player_cells.pop(cell.player_id, None)
        self.engine.remove_cell(cell_id)

    def _clamp_position(self, position: Vector) -> Vector:
        return (
            max(0.0, min(self.config.width, position[0])),
            max(0.0, min(self.config.height, position[1])),
        )

    def pop_events(self) -> List[dict]:
        events, self.events = self.events, []
        return events

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
        # Keep the simulation stable even if event loop hiccups for a frame.
        dt = max(1e-4, min(dt, MAX_DELTA_TIME))

        for cell in self.cells.values():
            target = self.targets.get(cell.player_id, cell.position)
            self.engine.set_cell_target(cell.id, target)

        collisions = self.engine.step(dt)

        self._handle_food_collisions()
        self._handle_cell_collisions(collisions)
        self._handle_self_merges()

    def _handle_food_collisions(self) -> None:
        consumed: List[str] = []
        for food in self.foods.values():
            for cell in self.cells.values():
                if _collides(cell.position, cell.radius, food.position, 3.0):
                    consumed.append(food.id)
                    cell.radius += food.value * 0.1
                    self.engine.update_radius(cell.id, cell.radius)
                    player = self.players.get(cell.player_id)
                    if player:
                        player.score += food.value
                        player.food_eaten += 1
                    break
        for food_id in consumed:
            self.foods.pop(food_id, None)
        self.populate_food()

    def _handle_cell_collisions(self, collisions: Iterable[CollisionEvent] | None = None) -> None:
        processed: set[tuple[str, str]] = set()
        collision_iterable = collisions or ()
        for event in collision_iterable:
            key = (event.first_id, event.second_id)
            if key in processed:
                continue
            processed.add(key)
            cell = self.cells.get(event.first_id)
            other = self.cells.get(event.second_id)
            if not cell or not other:
                continue
            if cell.player_id == other.player_id:
                continue
            if cell.radius >= other.radius * ABSORB_RATIO:
                self._absorb(cell, other)
            elif other.radius >= cell.radius * ABSORB_RATIO:
                self._absorb(other, cell)

        # Fallback sweep to catch slow overlaps the impulse solver handled
        # without reporting a collision (e.g. extremely gentle contacts).
        cells = list(self.cells.values())
        for i, cell in enumerate(cells):
            if cell.id not in self.cells:
                continue
            for j in range(i + 1, len(cells)):
                other = cells[j]
                if other.id not in self.cells:
                    continue
                if cell.player_id == other.player_id:
                    continue
                if _collides(cell.position, cell.radius, other.position, other.radius):
                    if cell.radius >= other.radius * ABSORB_RATIO:
                        self._absorb(cell, other)
                    elif other.radius >= cell.radius * ABSORB_RATIO:
                        self._absorb(other, cell)

    def _handle_self_merges(self) -> None:
        now = time.monotonic()
        for player_id, cell_ids in list(self.player_cells.items()):
            ids = [cid for cid in cell_ids if cid in self.cells]
            if len(ids) < 2:
                continue
            merged_ids: List[str] = []
            i = 0
            while i < len(ids):
                cell_a = self.cells.get(ids[i])
                if cell_a is None:
                    i += 1
                    continue
                j = i + 1
                while j < len(ids):
                    cell_b = self.cells.get(ids[j])
                    if cell_b is None:
                        j += 1
                        continue
                    if now < cell_a.merge_ready_at or now < cell_b.merge_ready_at:
                        j += 1
                        continue
                    if _collides(
                        cell_a.position,
                        cell_a.radius,
                        cell_b.position,
                        cell_b.radius * MERGE_DISTANCE_FACTOR,
                    ):
                        self._merge_cells(cell_a, cell_b)
                        merged_ids.append(ids[j])
                        ids.pop(j)
                        continue
                    j += 1
                i += 1
            if merged_ids:
                self.player_cells[player_id] = [cid for cid in ids if cid in self.cells]

    def _merge_cells(self, primary: Cell, secondary: Cell) -> None:
        area_primary = primary.area()
        area_secondary = secondary.area()
        total_area = area_primary + area_secondary
        if total_area > 0:
            primary.position = self._clamp_position(
                (
                    (primary.position[0] * area_primary + secondary.position[0] * area_secondary) / total_area,
                    (primary.position[1] * area_primary + secondary.position[1] * area_secondary) / total_area,
                )
            )
        primary.radius = math.sqrt(total_area / math.pi)
        primary.merge_ready_at = time.monotonic() + MERGE_DELAY
        self.engine.teleport(primary.id, primary.position)
        self.engine.update_radius(primary.id, primary.radius)
        self._remove_cell(secondary.id)

    def split_player(self, player_id: str) -> None:
        now = time.monotonic()
        cooldown_until = self.split_cooldowns.get(player_id, 0.0)
        if now < cooldown_until:
            return
        cell_ids = list(self.player_cells.get(player_id, []))
        if not cell_ids:
            return
        if len(cell_ids) >= 8:
            return
        largest_id = max(
            cell_ids,
            key=lambda cid: self.cells[cid].radius if cid in self.cells else 0.0,
        )
        cell = self.cells.get(largest_id)
        if not cell or cell.radius < SPLIT_MIN_RADIUS:
            return

        new_area = cell.area() / 2.0
        new_radius = math.sqrt(new_area / math.pi)
        if new_radius < SPLIT_MIN_RADIUS / 2:
            return

        origin = cell.position
        target = self.targets.get(player_id, origin)
        dx = target[0] - origin[0]
        dy = target[1] - origin[1]
        distance = math.hypot(dx, dy)
        if distance < 1e-3:
            angle = (uuid4().int % 360) * math.pi / 180.0
            direction = (math.cos(angle), math.sin(angle))
        else:
            direction = (dx / distance, dy / distance)

        separation_distance = new_radius * 2.4
        retreat_distance = new_radius * 0.8

        new_position = self._clamp_position(
            (
                origin[0] - direction[0] * retreat_distance,
                origin[1] - direction[1] * retreat_distance,
            )
        )
        cell.position = new_position
        cell.radius = new_radius
        self.engine.teleport(cell.id, new_position)
        self.engine.update_radius(cell.id, new_radius)
        cell.merge_ready_at = now + MERGE_DELAY

        new_mass = max(new_radius * new_radius, 1.0)
        base_speed = max(MIN_TARGET_SPEED, BASE_TARGET_SPEED / (new_mass ** MASS_SPEED_EXPONENT))
        impulse = base_speed * BOOST_SPEED_MULTIPLIER
        impulse_vx = direction[0] * impulse
        impulse_vy = direction[1] * impulse

        self.engine.apply_impulse(cell.id, (-impulse_vx, -impulse_vy))

        new_cell_position = self._clamp_position(
            (
                origin[0] + direction[0] * separation_distance,
                origin[1] + direction[1] * separation_distance,
            )
        )

        new_cell = Cell(
            id=uuid4().hex,
            player_id=player_id,
            position=new_cell_position,
            radius=new_radius,
            velocity=(0.0, 0.0),
            merge_ready_at=now + MERGE_DELAY,
        )
        self.cells[new_cell.id] = new_cell
        self.player_cells.setdefault(player_id, []).append(new_cell.id)
        self.engine.add_cell(new_cell, owner_id=player_id)
        self.engine.teleport(new_cell.id, new_cell_position)
        self.engine.apply_impulse(new_cell.id, (impulse_vx, impulse_vy))
        split_target = self.targets.get(player_id, new_cell_position)
        self.engine.set_cell_target(cell.id, split_target)
        self.engine.set_cell_target(new_cell.id, split_target)
        self.split_cooldowns[player_id] = now + SPLIT_COOLDOWN

    def _absorb(self, winner: Cell, loser: Cell) -> None:
        winner_area = winner.area()
        loser_area = loser.area() * 0.8
        total_area = winner_area + loser_area
        if total_area > 0:
            weight_winner = winner_area / total_area
            weight_loser = loser_area / total_area
            winner.position = self._clamp_position(
                (
                    winner.position[0] * weight_winner + loser.position[0] * weight_loser,
                    winner.position[1] * weight_winner + loser.position[1] * weight_loser,
                )
            )
        winner.radius = math.sqrt(total_area / math.pi)
        winner.merge_ready_at = time.monotonic() + MERGE_DELAY
        self.engine.teleport(winner.id, winner.position)
        self.engine.update_radius(winner.id, winner.radius)
        winner_player = self.players.get(winner.player_id)
        loser_player = self.players.get(loser.player_id)
        if winner_player:
            winner_player.cells_eaten += 1
        self._remove_cell(loser.id)
        if not self.player_cells.get(loser.player_id):
            if loser_player:
                self.events.append(
                    {
                        "type": "player_eliminated",
                        "winner_id": winner.player_id,
                        "loser_id": loser.player_id,
                        "winner_name": winner_player.name if winner_player else None,
                        "loser_name": loser_player.name if loser_player else None,
                    }
                )
            self.players.pop(loser.player_id, None)
            self.targets.pop(loser.player_id, None)
            self.split_cooldowns.pop(loser.player_id, None)

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
        self._event_listeners: List[Callable[[str, dict], Awaitable[None]]] = []

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

    async def split_player(self, world_id: str, player_id: str) -> None:
        state = await self.get_world(world_id)
        if state:
            state.split_player(player_id)

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

    def register_event_listener(self, listener: Callable[[str, dict], Awaitable[None]]) -> None:
        self._event_listeners.append(listener)

    async def _run_world(self, world_id: str, ctx: WorldContext) -> None:
        state = ctx.state
        while True:
            now = time.monotonic()
            raw_dt = now - state.last_update
            state.last_update = now
            dt = min(raw_dt, MAX_DELTA_TIME)
            state.tick(dt)
            events = state.pop_events()
            snapshot = state.snapshot()
            listeners = list(ctx.listeners)
            for queue in listeners:
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                await queue.put(snapshot)
            for event in events:
                for listener in list(self._event_listeners):
                    await listener(world_id, event)
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
                state.engine.resize_world(state.config.width, state.config.height)
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
