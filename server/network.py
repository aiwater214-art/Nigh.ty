"""FastAPI application exposing HTTP and WebSocket APIs."""
from __future__ import annotations

import asyncio
import os
import secrets
from contextlib import asynccontextmanager, suppress
from typing import Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from .config import ConfigService, load_config_from_database
from .player import Player
from .world import WorldManager, WorldSnapshotRepository
from app.core.database import SessionLocal
from app.crud import get_gameplay_config


class Settings(BaseModel):
    dashboard_api_key: str
    snapshot_dir: str = "data/snapshots"
    tick_rate: float = 30.0

    @classmethod
    def load(cls) -> "Settings":
        key = os.getenv("DASHBOARD_API_KEY")
        if not key:
            raise RuntimeError("DASHBOARD_API_KEY is required")
        snapshot_dir = os.getenv("SNAPSHOT_DIR", "data/snapshots")
        return cls(dashboard_api_key=key, snapshot_dir=snapshot_dir)


class LoginRequest(BaseModel):
    username: str
    dashboard_token: str


class LoginResponse(BaseModel):
    token: str
    username: str


class CreateWorldRequest(BaseModel):
    name: str


class TokenStore:
    """Stores issued login tokens."""

    def __init__(self) -> None:
        self._tokens: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def issue_token(self, username: str) -> str:
        async with self._lock:
            token = secrets.token_hex(16)
            self._tokens[token] = username
            return token

    async def validate(self, token: str) -> Optional[str]:
        async with self._lock:
            return self._tokens.get(token)


class ConnectionHub:
    """Tracks active websocket connections per world."""

    def __init__(self) -> None:
        self._connections: Dict[str, Dict[str, WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def register(self, world_id: str, player_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.setdefault(world_id, {})[player_id] = websocket

    async def unregister(self, world_id: str, player_id: str) -> None:
        async with self._lock:
            players = self._connections.get(world_id)
            if players and player_id in players:
                players.pop(player_id)
                if not players:
                    self._connections.pop(world_id, None)

    async def broadcast(self, world_id: str, message: dict) -> None:
        players = list((await self._get_world_connections(world_id)).items())
        for player_id, websocket in players:
            try:
                await websocket.send_json(message)
            except RuntimeError:
                await self.unregister(world_id, player_id)

    async def _get_world_connections(self, world_id: str) -> Dict[str, WebSocket]:
        async with self._lock:
            return dict(self._connections.get(world_id, {}))

    async def broadcast_global(self, message: dict) -> None:
        async with self._lock:
            snapshot = {
                world_id: dict(players)
                for world_id, players in self._connections.items()
            }
        for world_id, players in snapshot.items():
            for player_id, websocket in list(players.items()):
                try:
                    await websocket.send_json(message)
                except RuntimeError:
                    await self.unregister(world_id, player_id)


async def create_dependencies() -> tuple[WorldManager, TokenStore, ConnectionHub, Settings, ConfigService]:
    load_dotenv()
    settings = Settings.load()
    snapshot_repo = WorldSnapshotRepository(settings.snapshot_dir)
    world_manager = WorldManager(snapshot_repo, default_tick_rate=settings.tick_rate)
    token_store = TokenStore()
    connection_hub = ConnectionHub()
    
    def fetch_sync() -> dict:
        db = SessionLocal()
        try:
            config = get_gameplay_config(db)
            return config.as_dict()
        finally:
            db.close()

    async def fetch_config() -> dict:
        return await load_config_from_database(fetch_sync)

    async def apply_config(values: dict) -> None:
        await world_manager.update_config(values)

    async def broadcast_config(values: dict) -> None:
        await connection_hub.broadcast_global({"type": "config_update", "config": values})

    config_service = ConfigService(
        fetch_config=fetch_config,
        apply_config=apply_config,
        broadcast=broadcast_config,
    )
    await config_service.start()
    return world_manager, token_store, connection_hub, settings, config_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    world_manager, token_store, connection_hub, settings, config_service = await create_dependencies()
    app.state.world_manager = world_manager
    app.state.token_store = token_store
    app.state.connection_hub = connection_hub
    app.state.settings = settings
    app.state.config_service = config_service
    yield
    await config_service.stop()


def get_world_manager(app: FastAPI = Depends()) -> WorldManager:  # type: ignore[override]
    return app.state.world_manager  # type: ignore[attr-defined]


def get_token_store(app: FastAPI = Depends()) -> TokenStore:  # type: ignore[override]
    return app.state.token_store  # type: ignore[attr-defined]


def get_settings(app: FastAPI = Depends()) -> Settings:  # type: ignore[override]
    return app.state.settings  # type: ignore[attr-defined]


def get_config_service(app: FastAPI = Depends()) -> ConfigService:  # type: ignore[override]
    return app.state.config_service  # type: ignore[attr-defined]


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"]
    )

    @app.post("/login", response_model=LoginResponse)
    async def login(payload: LoginRequest, settings: Settings = Depends(get_settings), token_store: TokenStore = Depends(get_token_store)):
        if payload.dashboard_token != settings.dashboard_api_key:
            raise HTTPException(status_code=401, detail="Dashboard token invalid")
        token = await token_store.issue_token(payload.username)
        return LoginResponse(token=token, username=payload.username)

    @app.get("/config")
    async def get_config(config_service: ConfigService = Depends(get_config_service)):
        return config_service.snapshot()

    @app.get("/worlds")
    async def list_worlds(token: str, world_manager: WorldManager = Depends(get_world_manager), token_store: TokenStore = Depends(get_token_store)):
        if not await token_store.validate(token):
            raise HTTPException(status_code=401, detail="Invalid token")
        return await world_manager.list_worlds()

    @app.post("/worlds")
    async def create_world(payload: CreateWorldRequest, token: str, world_manager: WorldManager = Depends(get_world_manager), token_store: TokenStore = Depends(get_token_store)):
        if not await token_store.validate(token):
            raise HTTPException(status_code=401, detail="Invalid token")
        return await world_manager.create_world(payload.name)

    @app.websocket("/ws/world/{world_id}")
    async def world_socket(websocket: WebSocket, world_id: str, token: str, player_name: str):
        world_manager: WorldManager = websocket.app.state.world_manager  # type: ignore[attr-defined]
        token_store: TokenStore = websocket.app.state.token_store  # type: ignore[attr-defined]
        connection_hub: ConnectionHub = websocket.app.state.connection_hub  # type: ignore[attr-defined]
        config_service: ConfigService = websocket.app.state.config_service  # type: ignore[attr-defined]

        username = await token_store.validate(token)
        if not username:
            await websocket.close(code=4401)
            return
        await websocket.accept()

        player = Player(name=player_name, token=token)
        cell = await world_manager.add_player(world_id, player)
        if not cell:
            await websocket.send_json({"type": "error", "message": "World not found"})
            await websocket.close()
            return
        await connection_hub.register(world_id, player.id, websocket)

        subscription = await world_manager.subscribe(world_id)
        reader_task: Optional[asyncio.Task] = None
        try:
            await websocket.send_json(
                {
                    "type": "joined",
                    "player": player.to_dict(),
                    "cell": cell.to_dict(),
                    "config": config_service.snapshot(),
                }
            )

            async def read_messages() -> None:
                while True:
                    data = await websocket.receive_json()
                    action = data.get("type")
                    if action == "set_target":
                        target = data.get("target")
                        if isinstance(target, list) and len(target) == 2:
                            await world_manager.set_target(
                                world_id,
                                player.id,
                                (float(target[0]), float(target[1])),
                            )

            reader_task = asyncio.create_task(read_messages())
            async for snapshot in subscription:
                await websocket.send_json({"type": "world", "state": snapshot})
        except WebSocketDisconnect:
            pass
        finally:
            if reader_task:
                reader_task.cancel()
                with suppress(asyncio.CancelledError):
                    await reader_task
            await subscription.close()
            await connection_hub.unregister(world_id, player.id)
            await world_manager.remove_player(world_id, player.id)

    return app
