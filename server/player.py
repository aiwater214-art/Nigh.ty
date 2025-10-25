"""Player domain model used by the world simulation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple
from uuid import uuid4


Color = Tuple[int, int, int]


def _default_color() -> Color:
    base = uuid4().int % 0xFFFFFF
    return ((base >> 16) & 0xFF, (base >> 8) & 0xFF, base & 0xFF)


@dataclass
class Player:
    """Represents a player and their associated cell."""

    name: str
    token: str
    id: str = field(default_factory=lambda: uuid4().hex)
    color: Color = field(default_factory=_default_color)
    score: float = 0.0
    food_eaten: int = 0
    cells_eaten: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "score": self.score,
        }
