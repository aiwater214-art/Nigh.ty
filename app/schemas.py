from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    username: Optional[str] = None


class UserBase(BaseModel):
    username: str
    email: EmailStr
    full_name: Optional[str] = None


class UserCreate(UserBase):
    password: str


class User(UserBase):
    id: int
    is_active: bool
    is_admin: bool
    created_at: datetime

    class Config:
        orm_mode = True


class UserStats(BaseModel):
    cells_eaten: int
    food_eaten: int
    worlds_explored: int
    sessions_played: int
    updated_at: datetime

    class Config:
        orm_mode = True


class UserWithStats(User):
    stats: Optional[UserStats]


class WorldBase(BaseModel):
    name: str
    description: Optional[str] = None
    active_players: int = 0


class World(WorldBase):
    id: int
    created_at: datetime

    class Config:
        orm_mode = True


class StatsUpdate(BaseModel):
    cells_eaten: Optional[int] = None
    food_eaten: Optional[int] = None
    worlds_explored: Optional[int] = None
    sessions_played: Optional[int] = None


class GameplayConfig(BaseModel):
    width: float
    height: float
    tick_rate: float
    food_count: int
    snapshot_interval: float
    updated_at: datetime

    class Config:
        orm_mode = True


class GameplayConfigUpdate(BaseModel):
    width: Optional[float] = None
    height: Optional[float] = None
    tick_rate: Optional[float] = None
    food_count: Optional[int] = None
    snapshot_interval: Optional[float] = None
