"""Custom physics engine for player-controlled cells.

The default physics in the original implementation relied on ad-hoc
per-frame adjustments to velocities and positions.  That approach made it
very hard to keep player cells stable when splitting, colliding or trying to
recombine.  The engine provided here gives us a deterministic integrator with
cohesion/separation forces and robust collision handling between cells.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple


Vector = Tuple[float, float]


# --- Tunable physics constants ------------------------------------------------

# Global clamps used by both the engine and the world tick loop.
MAX_DELTA_TIME = 1.0 / 20.0

# Steering behaviour
CONTROL_RESPONSE = 8.0  # how fast velocities align with the movement target
LINEAR_DAMPING = 0.08  # constant drag that damps lingering oscillations

# Same-player cohesion and spacing
OWNER_COHESION = 34.0
SEPARATION_RESPONSE = 0.65

# Collision handling between different players
COLLISION_RESTITUTION = 0.05
COLLISION_FRICTION = 0.85

# Movement characteristics relative to cell size
BASE_CELL_SPEED = 260.0
MIN_CELL_SPEED = 45.0
MASS_SLOWDOWN = 0.45


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
    """Deterministic physics simulation for Agar-like cells."""

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
            target=cell.position,
        )
        body.mass = max(body.radius * body.radius, 1.0)
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

    def set_velocity(self, cell_id: str, velocity: Vector) -> None:
        body = self._bodies.get(cell_id)
        if body:
            body.velocity = velocity

    def apply_impulse(self, cell_id: str, impulse: Vector) -> None:
        body = self._bodies.get(cell_id)
        if body:
            body.velocity = (
                body.velocity[0] + impulse[0],
                body.velocity[1] + impulse[1],
            )

    def update_radius(self, cell_id: str, radius: float) -> None:
        body = self._bodies.get(cell_id)
        if body:
            body.radius = radius
            body.mass = max(radius * radius, 1.0)

    def get_body(self, cell_id: str) -> PhysicsBody | None:
        return self._bodies.get(cell_id)

    def step(self, dt: float) -> List[CollisionEvent]:
        dt = max(1e-4, min(dt, MAX_DELTA_TIME))
        collisions: List[CollisionEvent] = []

        if not self._bodies:
            return collisions

        for body in self._bodies.values():
            # Ensure the body mirrors any radius/velocity tweaks that happened
            # externally since the previous frame (for example via split/merge).
            body.sync_from_cell()
            self._apply_control(body, dt)

        for body in self._bodies.values():
            body.velocity = (
                body.velocity[0] * (1.0 - LINEAR_DAMPING * dt),
                body.velocity[1] * (1.0 - LINEAR_DAMPING * dt),
            )
            body.position = (
                body.position[0] + body.velocity[0] * dt,
                body.position[1] + body.velocity[1] * dt,
            )
            body.position = self._clamp(body.position)

        # Handle inter-cell interactions.  We iterate a few times so that deep
        # overlaps resolve without exploding velocities.
        for _ in range(3):
            collisions = self._resolve_collisions(collisions)
            self._apply_owner_cohesion(dt)

        for body in self._bodies.values():
            max_speed = BASE_CELL_SPEED - body.radius * MASS_SLOWDOWN
            max_speed = max(MIN_CELL_SPEED, min(BASE_CELL_SPEED, max_speed))
            speed = math.hypot(body.velocity[0], body.velocity[1])
            if speed > max_speed * 1.8:
                scale = (max_speed * 1.8) / max(speed, 1e-4)
                body.velocity = (body.velocity[0] * scale, body.velocity[1] * scale)
            body.sync_to_cell()

        return collisions

    # -- Internal helpers ---------------------------------------------------

    def _apply_control(self, body: PhysicsBody, dt: float) -> None:
        tx, ty = body.target
        dx = tx - body.position[0]
        dy = ty - body.position[1]
        distance = math.hypot(dx, dy)

        max_speed = BASE_CELL_SPEED - body.radius * MASS_SLOWDOWN
        max_speed = max(MIN_CELL_SPEED, min(BASE_CELL_SPEED, max_speed))

        if distance > 1e-4:
            desired_vx = dx / distance * max_speed
            desired_vy = dy / distance * max_speed
        else:
            desired_vx = desired_vy = 0.0

        blend = min(1.0, dt * CONTROL_RESPONSE)
        body.velocity = (
            body.velocity[0] + (desired_vx - body.velocity[0]) * blend,
            body.velocity[1] + (desired_vy - body.velocity[1]) * blend,
        )

        speed = math.hypot(body.velocity[0], body.velocity[1])
        if speed > max_speed > 1e-4:
            scale = max_speed / speed
            body.velocity = (body.velocity[0] * scale, body.velocity[1] * scale)

    def _resolve_collisions(self, existing: Iterable[CollisionEvent]) -> List[CollisionEvent]:
        collisions: List[CollisionEvent] = list(existing)
        bodies = list(self._bodies.values())
        count = len(bodies)
        for i in range(count):
            first = bodies[i]
            for j in range(i + 1, count):
                second = bodies[j]
                dx = second.position[0] - first.position[0]
                dy = second.position[1] - first.position[1]
                distance_sq = dx * dx + dy * dy
                same_owner = first.owner_id == second.owner_id
                min_distance = first.radius + second.radius
                min_distance_sq = min_distance * min_distance
                if distance_sq >= min_distance_sq:
                    continue

                if distance_sq <= 1e-9:
                    nx, ny = 1.0, 0.0
                    distance = 0.0
                else:
                    distance = math.sqrt(distance_sq)
                    nx = dx / distance
                    ny = dy / distance

                penetration = min_distance - distance

                total_mass = first.mass + second.mass
                if total_mass <= 0.0:
                    total_mass = 1.0
                share_first = second.mass / total_mass
                share_second = first.mass / total_mass

                if same_owner:
                    separation = penetration * SEPARATION_RESPONSE
                    first.position = self._clamp(
                        (
                            first.position[0] - nx * separation * share_first,
                            first.position[1] - ny * separation * share_first,
                        )
                    )
                    second.position = self._clamp(
                        (
                            second.position[0] + nx * separation * share_second,
                            second.position[1] + ny * separation * share_second,
                        )
                    )
                    continue

                # Push bodies apart proportionally to their mass.
                correction = penetration * 0.5
                first.position = self._clamp(
                    (
                        first.position[0] - nx * correction * share_first,
                        first.position[1] - ny * correction * share_first,
                    )
                )
                second.position = self._clamp(
                    (
                        second.position[0] + nx * correction * share_second,
                        second.position[1] + ny * correction * share_second,
                    )
                )

                # Basic impulse resolution to damp the overlap along the collision normal.
                relative_vx = second.velocity[0] - first.velocity[0]
                relative_vy = second.velocity[1] - first.velocity[1]
                separating_velocity = relative_vx * nx + relative_vy * ny
                if separating_velocity < 0.0:
                    impulse_mag = -(1.0 + COLLISION_RESTITUTION) * separating_velocity
                    impulse_mag /= (1.0 / first.mass) + (1.0 / second.mass)
                    impulse_x = nx * impulse_mag
                    impulse_y = ny * impulse_mag
                    first.velocity = (
                        first.velocity[0] - impulse_x / first.mass,
                        first.velocity[1] - impulse_y / first.mass,
                    )
                    second.velocity = (
                        second.velocity[0] + impulse_x / second.mass,
                        second.velocity[1] + impulse_y / second.mass,
                    )

                # Tangential friction to reduce sliding.
                tangent_vx = relative_vx - separating_velocity * nx
                tangent_vy = relative_vy - separating_velocity * ny
                first.velocity = (
                    first.velocity[0] + tangent_vx * COLLISION_FRICTION * 0.5,
                    first.velocity[1] + tangent_vy * COLLISION_FRICTION * 0.5,
                )
                second.velocity = (
                    second.velocity[0] - tangent_vx * COLLISION_FRICTION * 0.5,
                    second.velocity[1] - tangent_vy * COLLISION_FRICTION * 0.5,
                )

                collisions.append(
                    CollisionEvent(
                        first_id=first.id,
                        second_id=second.id,
                        penetration=penetration,
                        normal=(nx, ny),
                    )
                )
        dedup: Dict[Tuple[str, str], CollisionEvent] = {}
        for event in collisions:
            a, b = event.first_id, event.second_id
            if a == b:
                continue
            if a > b:
                key = (b, a)
                dedup[key] = CollisionEvent(
                    first_id=b,
                    second_id=a,
                    penetration=event.penetration,
                    normal=(-event.normal[0], -event.normal[1]),
                )
            else:
                key = (a, b)
                existing_event = dedup.get(key)
                if existing_event is None or event.penetration > existing_event.penetration:
                    dedup[key] = event
        return list(dedup.values())

    def _apply_owner_cohesion(self, dt: float) -> None:
        by_owner: Dict[str, List[PhysicsBody]] = {}
        for body in self._bodies.values():
            by_owner.setdefault(body.owner_id, []).append(body)

        for bodies in by_owner.values():
            if len(bodies) < 2:
                continue
            total_mass = sum(body.mass for body in bodies)
            if total_mass <= 0.0:
                continue
            center_x = sum(body.position[0] * body.mass for body in bodies) / total_mass
            center_y = sum(body.position[1] * body.mass for body in bodies) / total_mass
            for body in bodies:
                dx = center_x - body.position[0]
                dy = center_y - body.position[1]
                distance = math.hypot(dx, dy)
                if distance <= 1e-4:
                    continue
                pull = min(distance, OWNER_COHESION * dt)
                body.velocity = (
                    body.velocity[0] + dx / distance * pull,
                    body.velocity[1] + dy / distance * pull,
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

