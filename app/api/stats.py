from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.core.database import get_db
from app.crud import update_user_stats
from app.schemas import StatsUpdate, UserStats

router = APIRouter()


@router.get("/me", response_model=UserStats)
def read_my_stats(current_user=Depends(get_current_active_user)):
    if current_user.stats is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stats not found")
    return current_user.stats


@router.put("/me", response_model=UserStats)
def update_my_stats(payload: StatsUpdate, current_user=Depends(get_current_active_user), db: Session = Depends(get_db)):
    stats = update_user_stats(
        db,
        current_user,
        cells_eaten=payload.cells_eaten,
        food_eaten=payload.food_eaten,
        worlds_explored=payload.worlds_explored,
        sessions_played=payload.sessions_played,
    )
    return stats


@router.get("/aggregate", response_model=dict)
def aggregate_stats(
    current_user=Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    from sqlalchemy import func
    from app.models import UserStats as UserStatsModel

    totals = db.query(
        func.sum(UserStatsModel.cells_eaten),
        func.sum(UserStatsModel.food_eaten),
        func.sum(UserStatsModel.worlds_explored),
        func.sum(UserStatsModel.sessions_played),
    ).one()
    return {
        "cells_eaten": totals[0] or 0,
        "food_eaten": totals[1] or 0,
        "worlds_explored": totals[2] or 0,
        "sessions_played": totals[3] or 0,
    }
