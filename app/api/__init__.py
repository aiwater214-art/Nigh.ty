from fastapi import APIRouter

from . import admin, auth, config, stats, worlds, users

api_router = APIRouter()
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(stats.router, prefix="/stats", tags=["stats"])
api_router.include_router(worlds.router, prefix="/worlds", tags=["worlds"])
api_router.include_router(config.router, tags=["config"])
api_router.include_router(admin.router, tags=["admin"])
