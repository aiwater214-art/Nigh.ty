from typing import Optional

from sqlalchemy.orm import Session

from app.core.security import get_password_hash, verify_password
from app.models import User, UserStats, World


def get_user_by_username(db: Session, username: str) -> Optional[User]:
    return db.query(User).filter(User.username == username).first()


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    return db.query(User).filter(User.email == email).first()


def create_user(db: Session, username: str, email: str, password: str, full_name: Optional[str] = None) -> User:
    hashed_password = get_password_hash(password)
    user = User(username=username, email=email, hashed_password=hashed_password, full_name=full_name)
    db.add(user)
    db.flush()
    stats = UserStats(user_id=user.id)
    db.add(stats)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
    user = get_user_by_username(db, username)
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user


def update_user_stats(
    db: Session,
    user: User,
    *,
    cells_eaten: Optional[int] = None,
    food_eaten: Optional[int] = None,
    worlds_explored: Optional[int] = None,
    sessions_played: Optional[int] = None,
) -> UserStats:
    stats = user.stats
    if stats is None:
        stats = UserStats(user_id=user.id)
        db.add(stats)
        db.flush()
    if cells_eaten is not None:
        stats.cells_eaten = cells_eaten
    if food_eaten is not None:
        stats.food_eaten = food_eaten
    if worlds_explored is not None:
        stats.worlds_explored = worlds_explored
    if sessions_played is not None:
        stats.sessions_played = sessions_played
    db.commit()
    db.refresh(stats)
    return stats


def list_worlds(db: Session) -> list[World]:
    return db.query(World).order_by(World.name).all()


def create_world(db: Session, name: str, description: str = "", active_players: int = 0) -> World:
    world = World(name=name, description=description, active_players=active_players)
    db.add(world)
    db.commit()
    db.refresh(world)
    return world
