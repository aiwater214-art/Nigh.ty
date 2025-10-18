from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin_user
from app.core.database import get_db
from app.core.events import CONFIG_CHANNEL, config_pubsub
from app.crud import get_gameplay_config, get_user_by_username, set_user_active, update_gameplay_config
from app.models import User as UserModel
from app.schemas import GameplayConfig, GameplayConfigUpdate, User

router = APIRouter(prefix="/admin")


@router.get("/users", response_model=list[User])
def list_users(db: Session = Depends(get_db), current_user=Depends(get_current_admin_user)):
    return db.query(UserModel).order_by(UserModel.username).all()


@router.post("/users/{username}/ban", response_model=User)
def ban_user(
    username: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin_user),
):
    target = get_user_by_username(db, username)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return set_user_active(db, target, False)


@router.post("/users/{username}/unban", response_model=User)
def unban_user(
    username: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin_user),
):
    target = get_user_by_username(db, username)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return set_user_active(db, target, True)


@router.patch("/config", response_model=GameplayConfig)
def update_config(
    update: GameplayConfigUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_admin_user),
):
    config = update_gameplay_config(
        db,
        width=update.width,
        height=update.height,
        tick_rate=update.tick_rate,
        food_count=update.food_count,
        snapshot_interval=update.snapshot_interval,
    )
    config_pubsub.publish(CONFIG_CHANNEL, config.as_dict())
    return config


@router.get("/config", response_model=GameplayConfig)
def get_admin_config(db: Session = Depends(get_db), current_user=Depends(get_current_admin_user)):
    return get_gameplay_config(db)

