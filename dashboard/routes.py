import json
from typing import AsyncIterator, Optional

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.events import CONFIG_CHANNEL, STATS_CHANNEL, config_pubsub, stats_pubsub
from app.core.security import create_access_token
from app.crud import (
    authenticate_user,
    create_user,
    get_gameplay_config,
    get_user_by_email,
    get_user_by_username,
    list_worlds,
    set_user_active,
    update_gameplay_config,
)
from app.models import User as UserModel, UserStats as UserStatsModel
from dashboard.deps import get_current_admin_user, get_current_user

router = APIRouter(prefix="/dashboard")
templates = Jinja2Templates(directory="dashboard/templates")


@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login")
def login_action(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = authenticate_user(db, username, password)
    if not user:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid username or password"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    token = create_access_token({"sub": user.username})
    request.session["token"] = token
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/logout")
def logout(request: Request):
    request.session.pop("token", None)
    return RedirectResponse(url="/dashboard/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/register")
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@router.post("/register")
def register_action(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(""),
    db: Session = Depends(get_db),
):
    if get_user_by_username(db, username) or get_user_by_email(db, email):
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "Username or email already registered"},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    create_user(db, username, email, password, full_name or None)
    return RedirectResponse(url="/dashboard/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/")
def dashboard_home(request: Request, user=Depends(get_current_user), db: Session = Depends(get_db)):
    stats = user.stats
    aggregate = db.query(
        UserStatsModel.cells_eaten,
        UserStatsModel.food_eaten,
        UserStatsModel.worlds_explored,
        UserStatsModel.sessions_played,
    ).all()
    totals = {
        "cells_eaten": sum((row[0] or 0) for row in aggregate),
        "food_eaten": sum((row[1] or 0) for row in aggregate),
        "worlds_explored": sum((row[2] or 0) for row in aggregate),
        "sessions_played": sum((row[3] or 0) for row in aggregate),
    }
    worlds = list_worlds(db)
    config = get_gameplay_config(db)
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "stats": stats,
            "totals": totals,
            "worlds": worlds,
            "config": config,
        },
    )


def _serialize_stats(stats: Optional[UserStatsModel]) -> dict:
    if not stats:
        return {
            "cells_eaten": 0,
            "food_eaten": 0,
            "worlds_explored": 0,
            "sessions_played": 0,
        }
    return {
        "cells_eaten": int(stats.cells_eaten or 0),
        "food_eaten": int(stats.food_eaten or 0),
        "worlds_explored": int(stats.worlds_explored or 0),
        "sessions_played": int(stats.sessions_played or 0),
    }


@router.get("/stats/stream", name="stats_stream")
async def stats_stream(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    def load_snapshot() -> dict:
        db.refresh(user)
        aggregate = db.query(
            UserStatsModel.cells_eaten,
            UserStatsModel.food_eaten,
            UserStatsModel.worlds_explored,
            UserStatsModel.sessions_played,
        ).all()
        totals = {
            "cells_eaten": sum((row[0] or 0) for row in aggregate),
            "food_eaten": sum((row[1] or 0) for row in aggregate),
            "worlds_explored": sum((row[2] or 0) for row in aggregate),
            "sessions_played": sum((row[3] or 0) for row in aggregate),
        }
        return {
            "stats": _serialize_stats(user.stats),
            "totals": totals,
        }

    async def event_stream() -> AsyncIterator[str]:
        snapshot = load_snapshot()
        yield f"event: stats\ndata: {json.dumps(snapshot)}\n\n"

        async with stats_pubsub.subscribe(STATS_CHANNEL) as queue:
            while True:
                payload = await queue.get()
                if await request.is_disconnected():
                    break
                if not isinstance(payload, dict):
                    continue
                message: dict = {"totals": payload.get("totals", {})}
                if payload.get("username") == user.username and payload.get("stats"):
                    message["stats"] = payload["stats"]
                yield f"event: stats\ndata: {json.dumps(message)}\n\n"

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)


@router.get("/admin")
def admin_home(request: Request, user=Depends(get_current_admin_user), db: Session = Depends(get_db)):
    users = db.query(UserModel).order_by(UserModel.username).all()
    config = get_gameplay_config(db)
    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "user": user,
            "users": users,
            "config": config,
        },
    )


@router.post("/admin/users/{username}/toggle")
def toggle_user(
    request: Request,
    username: str,
    user=Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    target = get_user_by_username(db, username)
    if target:
        set_user_active(db, target, not target.is_active)
    return RedirectResponse(url="/dashboard/admin", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/admin/config")
def update_admin_config(
    request: Request,
    width: float = Form(...),
    height: float = Form(...),
    tick_rate: float = Form(...),
    food_count: int = Form(...),
    snapshot_interval: float = Form(...),
    user=Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    config = update_gameplay_config(
        db,
        width=width,
        height=height,
        tick_rate=tick_rate,
        food_count=food_count,
        snapshot_interval=snapshot_interval,
    )
    config_pubsub.publish(CONFIG_CHANNEL, config.as_dict())
    return RedirectResponse(url="/dashboard/admin", status_code=status.HTTP_303_SEE_OTHER)
