"""Server package exposing FastAPI application factory."""
from .network import create_app

__all__ = ["create_app"]
