"""FastAPI application exposing HTTP and WebSocket APIs."""

import asyncio
import os
import secrets
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Annotated, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from .config import ConfigService, load_config_from_database
from .player import Player
from .world import WorldManager, WorldSnapshotRepository
from sqlalchemy import func

from sqlalchemy.exc import OperationalError

from app.core.database import Base, SessionLocal, engine
from app.crud import authenticate_user, get_gameplay_config, get_user_by_username
from app.models import UserStats
from app.core.events import STATS_CHANNEL, stats_pubsub


class Settings(BaseModel):
    dashboard_api_key: Optional[str] = None
    snapshot_dir: str = "data/snapshots"
    tick_rate: float = 30.0

    @classmethod
    def load(cls) -> "Settings":
        key = os.getenv("DASHBOARD_API_KEY")
        snapshot_dir = os.getenv("SNAPSHOT_DIR", "data/snapshots")
        return cls(dashboard_api_key=key, snapshot_dir=snapshot_dir)


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    username: str


class CreateWorldRequest(BaseModel):
    name: str


@dataclass
class TokenInfo:
    username: str
    user_id: int


class TokenStore:
    """Stores issued login tokens."""

    def __init__(self) -> None:
        self._tokens: Dict[str, TokenInfo] = {}
        self._lock = asyncio.Lock()

    async def issue_token(self, username: str, user_id: int) -> str:
        async with self._lock:
            token = secrets.token_hex(16)
            self._tokens[token] = TokenInfo(username=username, user_id=user_id)
            return token

    async def validate(self, token: str) -> Optional[TokenInfo]:
        async with self._lock:
            return self._tokens.get(token)


class StatsService:
    """Asynchronously persist incremental player statistics."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def add_progress(
        self,
        username: str,
        *,
        cells_eaten: int = 0,
        food_eaten: int = 0,
        worlds_explored: int = 0,
        sessions_played: int = 0,
    ) -> None:
        if not any([cells_eaten, food_eaten, worlds_explored, sessions_played]):
            return

        async with self._lock:
            def worker() -> tuple[Optional[UserStats], Optional[dict]]:
                db = SessionLocal()
                try:
                    user = get_user_by_username(db, username)
                    if not user or not user.is_active:
                        totals = db.query(
                            func.sum(UserStats.cells_eaten),
                            func.sum(UserStats.food_eaten),
                            func.sum(UserStats.worlds_explored),
                            func.sum(UserStats.sessions_played),
                        ).one()
                        return None, {
                            "cells_eaten": int(totals[0] or 0),
                            "food_eaten": int(totals[1] or 0),
                            "worlds_explored": int(totals[2] or 0),
                            "sessions_played": int(totals[3] or 0),
                        }

                    stats = user.stats
                    if stats is None:
                        stats = UserStats(user_id=user.id)
                        db.add(stats)
                        db.flush()
                    stats.cells_eaten += cells_eaten
                    stats.food_eaten += food_eaten
                    stats.worlds_explored += worlds_explored
                    stats.sessions_played += sessions_played
                    db.add(stats)
                    db.commit()
                    db.refresh(stats)

                    totals = db.query(
                        func.sum(UserStats.cells_eaten),
                        func.sum(UserStats.food_eaten),
                        func.sum(UserStats.worlds_explored),
                        func.sum(UserStats.sessions_played),
                    ).one()

                    return stats, {
                        "cells_eaten": int(totals[0] or 0),
                        "food_eaten": int(totals[1] or 0),
                        "worlds_explored": int(totals[2] or 0),
                        "sessions_played": int(totals[3] or 0),
                    }
                finally:
                    db.close()

            stats_obj, totals = await asyncio.to_thread(worker)

        if totals is None:
            return

        payload = {"username": username, "totals": totals}
        if stats_obj is not None:
            payload["stats"] = {
                "cells_eaten": int(stats_obj.cells_eaten),
                "food_eaten": int(stats_obj.food_eaten),
                "worlds_explored": int(stats_obj.worlds_explored),
                "sessions_played": int(stats_obj.sessions_played),
            }

        stats_pubsub.publish(STATS_CHANNEL, payload)


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

    async def _get_connection(self, world_id: str, player_id: str) -> Optional[WebSocket]:
        async with self._lock:
            return self._connections.get(world_id, {}).get(player_id)

    async def send_to(self, world_id: str, player_id: str, message: dict) -> None:
        websocket = await self._get_connection(world_id, player_id)
        if websocket is None:
            return
        try:
            await websocket.send_json(message)
        except RuntimeError:
            await self.unregister(world_id, player_id)

    async def close(self, world_id: str, player_id: str, *, code: int = 1000, reason: Optional[str] = None) -> None:
        websocket = await self._get_connection(world_id, player_id)
        if websocket is None:
            return
        try:
            await websocket.close(code=code, reason=reason)
        except RuntimeError:
            pass
        finally:
            await self.unregister(world_id, player_id)

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


async def create_dependencies() -> tuple[
    WorldManager,
    TokenStore,
    ConnectionHub,
    Settings,
    ConfigService,
    StatsService,
]:
    load_dotenv()
    settings = Settings.load()
    snapshot_repo = WorldSnapshotRepository(settings.snapshot_dir)
    world_manager = WorldManager(snapshot_repo, default_tick_rate=settings.tick_rate)
    token_store = TokenStore()
    connection_hub = ConnectionHub()
    stats_service = StatsService()
    
    Base.metadata.create_all(bind=engine)

    def fetch_sync() -> dict:
        db = SessionLocal()
        try:
            try:
                config = get_gameplay_config(db)
            except OperationalError:
                db.rollback()
                Base.metadata.create_all(bind=engine)
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
    
    async def handle_world_event(world_id: str, event: dict) -> None:
        if event.get("type") == "player_eliminated":
            loser_id = event.get("loser_id")
            if not loser_id:
                return
            message = {
                "type": "eliminated",
                "by": event.get("winner_name"),
                "world": world_id,
            }
            await connection_hub.send_to(world_id, loser_id, message)
            await connection_hub.close(world_id, loser_id, code=4404, reason="Eliminated")

    world_manager.register_event_listener(handle_world_event)
    await config_service.start()
    return world_manager, token_store, connection_hub, settings, config_service, stats_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    world_manager, token_store, connection_hub, settings, config_service, stats_service = await create_dependencies()
    app.state.world_manager = world_manager
    app.state.token_store = token_store
    app.state.connection_hub = connection_hub
    app.state.settings = settings
    app.state.config_service = config_service
    app.state.stats_service = stats_service
    yield
    await config_service.stop()


def get_world_manager(request: Request) -> WorldManager:  # type: ignore[override]
    return request.app.state.world_manager  # type: ignore[attr-defined]


def get_token_store(request: Request) -> TokenStore:  # type: ignore[override]
    return request.app.state.token_store  # type: ignore[attr-defined]


def get_settings(request: Request) -> Settings:  # type: ignore[override]
    return request.app.state.settings  # type: ignore[attr-defined]


def get_config_service(request: Request) -> ConfigService:  # type: ignore[override]
    return request.app.state.config_service  # type: ignore[attr-defined]


def get_stats_service(request: Request) -> StatsService:  # type: ignore[override]
    return request.app.state.stats_service  # type: ignore[attr-defined]


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"]
    )

    TokenStoreDep = Annotated[TokenStore, Depends(get_token_store)]
    WorldManagerDep = Annotated[WorldManager, Depends(get_world_manager)]
    ConfigServiceDep = Annotated[ConfigService, Depends(get_config_service)]

    @app.post("/login", response_model=LoginResponse)
    async def login(payload: LoginRequest, token_store: TokenStoreDep):
        db = SessionLocal()
        try:
            user = authenticate_user(db, payload.username, payload.password)
        finally:
            db.close()
        if not user:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        token = await token_store.issue_token(user.username, user.id)
        return LoginResponse(token=token, username=user.username)

    @app.get("/config")
    async def get_config(config_service: ConfigServiceDep):
        return config_service.snapshot()

    @app.get("/worlds")
    async def list_worlds(token: str, world_manager: WorldManagerDep, token_store: TokenStoreDep):
        if not await token_store.validate(token):
            raise HTTPException(status_code=401, detail="Invalid token")
        return await world_manager.list_worlds()

    @app.post("/worlds")
    async def create_world(payload: CreateWorldRequest, token: str, world_manager: WorldManagerDep, token_store: TokenStoreDep):
        if not await token_store.validate(token):
            raise HTTPException(status_code=401, detail="Invalid token")
        return await world_manager.create_world(payload.name)

    @app.websocket("/ws/world/{world_id}")
    async def world_socket(websocket: WebSocket, world_id: str, token: str, player_name: str):
        world_manager: WorldManager = websocket.app.state.world_manager  # type: ignore[attr-defined]
        token_store: TokenStore = websocket.app.state.token_store  # type: ignore[attr-defined]
        connection_hub: ConnectionHub = websocket.app.state.connection_hub  # type: ignore[attr-defined]
        config_service: ConfigService = websocket.app.state.config_service  # type: ignore[attr-defined]
        stats_service: StatsService = websocket.app.state.stats_service  # type: ignore[attr-defined]

        token_info = await token_store.validate(token)
        if not token_info:
            await websocket.close(code=4401)
            return
        username = token_info.username
        await websocket.accept()

        player = Player(name=player_name, token=token)
        cell = await world_manager.add_player(world_id, player)
        if not cell:
            await websocket.send_json({"type": "error", "message": "World not found"})
            await websocket.close()
            return
        await connection_hub.register(world_id, player.id, websocket)
        await stats_service.add_progress(username, sessions_played=1, worlds_explored=1)

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
                    elif action == "split":
                        await world_manager.split_player(world_id, player.id)

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
            await stats_service.add_progress(
                username,
                food_eaten=player.food_eaten,
                cells_eaten=player.cells_eaten,
            )

    return app
