"""Simple custom physics tuned for an Agar.io style experience.

The previous iteration attempted to mimic rigid-body dynamics with impulse
responses.  While technically interesting it proved jittery for lightweight
cells and hard to predict during splits.  The implementation below trades the
general solver for deterministic steering similar to the reference
implementation the user provided.  Each cell continuously steers towards its
target with mass-dependent speed and light inertia.  Splits apply temporary
impulses that decay naturally, cells owned by the same player maintain a soft
spacing ring, and overlapping opponents are separated in a single pass before
collision events are reported back to the world layer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


Vector = Tuple[float, float]


# --- Tunable physics constants ------------------------------------------------

MAX_DELTA_TIME = 1.0 / 30.0

# Movement tuning – roughly approximates the behaviour in the reference Agar
# clone while remaining deterministic regardless of the frame-rate.
BASE_TARGET_SPEED = 520.0
MIN_TARGET_SPEED = 48.0
MASS_SPEED_EXPONENT = 0.42
BOOST_SPEED_MULTIPLIER = 2.3
IMPULSE_DECAY_RATE = 6.0

# Same-owner spacing keeps cells gently separated while still allowing them to
# touch visually.  A handful of relaxation passes keeps large stacks stable.
OWNER_SPACING_FACTOR = 0.95
RELAXATION_PASSES = 4


@dataclass
class CollisionEvent:
    """Collision detected between two cells after resolution."""

    first_id: str
    second_id: str
    penetration: float
    normal: Vector


@dataclass
class PhysicsBody:
    """Mutable physical state that mirrors a :class:`server.world.Cell`."""

    id: str
    owner_id: str
    cell: "CellLike"
    position: Vector
    velocity: Vector = (0.0, 0.0)
    radius: float = 0.0
    target: Vector = (0.0, 0.0)
    mass: float = field(default=1.0)
    impulse: Vector = (0.0, 0.0)
    control_velocity: Vector = (0.0, 0.0)

    def sync_from_cell(self) -> None:
        self.position = self.cell.position
        self.radius = self.cell.radius
        self.mass = max(self.radius * self.radius, 1.0)
        self.velocity = self.cell.velocity

    def sync_to_cell(self) -> None:
        self.cell.position = self.position
        self.cell.velocity = self.velocity
        self.cell.radius = self.radius


class PhysicsEngine:
    """Custom steering-based physics tuned for Agar.io style gameplay."""

    def __init__(self, width: float, height: float) -> None:
        self._width = width
        self._height = height
        self._bodies: Dict[str, PhysicsBody] = {}

    # -- Public API ---------------------------------------------------------

    def resize_world(self, width: float, height: float) -> None:
        self._width = width
        self._height = height

    def add_cell(self, cell: "CellLike", owner_id: str) -> None:
        body = PhysicsBody(
            id=cell.id,
            owner_id=owner_id,
            cell=cell,
            position=self._clamp(cell.position),
            radius=cell.radius,
            target=self._clamp(cell.position),
        )
        body.mass = max(body.radius * body.radius, 1.0)
        body.velocity = cell.velocity
        body.impulse = cell.velocity
        self._bodies[cell.id] = body
        body.sync_to_cell()

    def remove_cell(self, cell_id: str) -> None:
        self._bodies.pop(cell_id, None)

    def set_cell_target(self, cell_id: str, target: Vector) -> None:
        body = self._bodies.get(cell_id)
        if body:
            body.target = self._clamp(target)

    def teleport(self, cell_id: str, position: Vector) -> None:
        body = self._bodies.get(cell_id)
        if body:
            body.position = self._clamp(position)
            body.sync_to_cell()

    def set_velocity(self, cell_id: str, velocity: Vector) -> None:
        body = self._bodies.get(cell_id)
        if body:
            body.velocity = velocity
            body.impulse = velocity
            body.sync_to_cell()

    def apply_impulse(self, cell_id: str, impulse: Vector) -> None:
        body = self._bodies.get(cell_id)
        if body:
            body.impulse = (
                body.impulse[0] + impulse[0],
                body.impulse[1] + impulse[1],
            )
            body.velocity = (
                body.velocity[0] + impulse[0],
                body.velocity[1] + impulse[1],
            )
            body.sync_to_cell()

    def update_radius(self, cell_id: str, radius: float) -> None:
        body = self._bodies.get(cell_id)
        if body:
            body.radius = radius
            body.mass = max(radius * radius, 1.0)
            body.sync_to_cell()

    def get_body(self, cell_id: str) -> PhysicsBody | None:
        return self._bodies.get(cell_id)

    def step(self, dt: float) -> List[CollisionEvent]:
        dt = max(1e-4, min(dt, MAX_DELTA_TIME))
        if not self._bodies:
            return []

        for body in self._bodies.values():
            body.sync_from_cell()

        for body in self._bodies.values():
            body.control_velocity = self._compute_target_velocity(body)

        collisions: Dict[Tuple[str, str], CollisionEvent] = {}

        for body in self._bodies.values():
            self._integrate_motion(body, dt)

        for _ in range(RELAXATION_PASSES):
            self._apply_owner_spacing()
            self._resolve_overlaps(collisions)
            for body in self._bodies.values():
                body.position = self._clamp(body.position)

        for body in self._bodies.values():
            body.sync_to_cell()

        return list(collisions.values())

    # -- Internal helpers ---------------------------------------------------

    def _compute_target_velocity(self, body: PhysicsBody) -> Vector:
        tx, ty = body.target
        dx = tx - body.position[0]
        dy = ty - body.position[1]
        distance = math.hypot(dx, dy)
        if distance <= 1e-6:
            return (0.0, 0.0)

        direction = (dx / distance, dy / distance)
        target_speed = self._speed_for_mass(body.mass)
        return (direction[0] * target_speed, direction[1] * target_speed)

    def _integrate_motion(self, body: PhysicsBody, dt: float) -> None:
        vx = body.control_velocity[0] + body.impulse[0]
        vy = body.control_velocity[1] + body.impulse[1]

        max_speed = self._speed_for_mass(body.mass) * BOOST_SPEED_MULTIPLIER
        speed = math.hypot(vx, vy)
        if speed > max_speed:
            scale = max_speed / max(speed, 1e-6)
            vx *= scale
            vy *= scale

        body.velocity = (vx, vy)
        body.position = (
            body.position[0] + vx * dt,
            body.position[1] + vy * dt,
        )
        body.position = self._clamp(body.position)

        decay = math.exp(-IMPULSE_DECAY_RATE * dt)
        body.impulse = (body.impulse[0] * decay, body.impulse[1] * decay)

    def _speed_for_mass(self, mass: float) -> float:
        adjusted_mass = max(mass, 1.0)
        speed = BASE_TARGET_SPEED / (adjusted_mass ** MASS_SPEED_EXPONENT)
        return max(MIN_TARGET_SPEED, speed)

    def _apply_owner_spacing(self) -> None:
        owners: Dict[str, List[PhysicsBody]] = {}
        for body in self._bodies.values():
            owners.setdefault(body.owner_id, []).append(body)

        for bodies in owners.values():
            if len(bodies) < 2:
                continue
            for i in range(len(bodies)):
                first = bodies[i]
                for j in range(i + 1, len(bodies)):
                    second = bodies[j]
                    self._separate_pair(first, second, OWNER_SPACING_FACTOR)

    def _separate_pair(self, first: PhysicsBody, second: PhysicsBody, factor: float) -> None:
        min_distance = (first.radius + second.radius) * factor
        dx = second.position[0] - first.position[0]
        dy = second.position[1] - first.position[1]
        distance_sq = dx * dx + dy * dy
        if distance_sq >= max(min_distance * min_distance, 1e-9):
            return

        if distance_sq <= 1e-9:
            # Deterministic fallback direction based on ids without relying on
            # Python's randomized hash.
            seed = sum(ord(ch) for ch in (first.id + second.id))
            angle = (seed % 360) * math.pi / 180.0
            nx = math.cos(angle)
            ny = math.sin(angle)
            distance = 0.0
        else:
            distance = math.sqrt(distance_sq)
            nx = dx / distance
            ny = dy / distance

        penetration = min_distance - distance
        if penetration <= 0.0:
            return

        total_mass = max(first.mass + second.mass, 1.0)
        share_first = second.mass / total_mass
        share_second = first.mass / total_mass

        first.position = (
            first.position[0] - nx * penetration * share_first,
            first.position[1] - ny * penetration * share_first,
        )
        second.position = (
            second.position[0] + nx * penetration * share_second,
            second.position[1] + ny * penetration * share_second,
        )

        relative_vx = second.velocity[0] - first.velocity[0]
        relative_vy = second.velocity[1] - first.velocity[1]
        vn = relative_vx * nx + relative_vy * ny
        if vn < 0.0:
            adjust = vn * 0.5
            first.velocity = (
                first.velocity[0] + nx * adjust,
                first.velocity[1] + ny * adjust,
            )
            second.velocity = (
                second.velocity[0] - nx * adjust,
                second.velocity[1] - ny * adjust,
            )

    def _resolve_overlaps(self, collisions: Dict[Tuple[str, str], CollisionEvent]) -> None:
        bodies = list(self._bodies.values())
        for i in range(len(bodies)):
            first = bodies[i]
            for j in range(i + 1, len(bodies)):
                second = bodies[j]
                if first.owner_id == second.owner_id:
                    continue
                dx = second.position[0] - first.position[0]
                dy = second.position[1] - first.position[1]
                min_distance = first.radius + second.radius
                distance_sq = dx * dx + dy * dy
                if distance_sq >= min_distance * min_distance:
                    continue

                if distance_sq <= 1e-9:
                    nx, ny = 1.0, 0.0
                    distance = 0.0
                else:
                    distance = math.sqrt(distance_sq)
                    nx = dx / distance
                    ny = dy / distance

                penetration = min_distance - distance

                total_mass = max(first.mass + second.mass, 1.0)
                share_first = second.mass / total_mass
                share_second = first.mass / total_mass

                first.position = (
                    first.position[0] - nx * penetration * share_first,
                    first.position[1] - ny * penetration * share_first,
                )
                second.position = (
                    second.position[0] + nx * penetration * share_second,
                    second.position[1] + ny * penetration * share_second,
                )

                key = (first.id, second.id) if first.id < second.id else (second.id, first.id)
                collisions.setdefault(
                    key,
                    CollisionEvent(
                        first_id=first.id,
                        second_id=second.id,
                        penetration=penetration,
                        normal=(nx, ny),
                    ),
                )

    def _clamp(self, position: Vector) -> Vector:
        return (
            max(0.0, min(self._width, position[0])),
            max(0.0, min(self._height, position[1])),
        )


class CellLike:
    """Protocol-style helper to satisfy type checkers without importing Player."""

    id: str
    position: Vector
    velocity: Vector
    radius: float

