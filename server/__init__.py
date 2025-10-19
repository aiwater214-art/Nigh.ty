"""Server package exposing FastAPI application factory."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .network import create_app as _create_app

__all__ = ["create_app"]


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from .network import create_app

        return create_app
    raise AttributeError(f"module 'server' has no attribute {name!r}")
