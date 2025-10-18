from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.core.database import get_db
from app.crud import create_world, list_worlds
from app.schemas import World, WorldBase

router = APIRouter(dependencies=[Depends(get_current_active_user)])


@router.get("/", response_model=list[World])
def read_worlds(db: Session = Depends(get_db)):
    return list_worlds(db)


@router.post("/", response_model=World, status_code=201)
def create_world_endpoint(world: WorldBase, db: Session = Depends(get_db)):
    return create_world(db, world.name, world.description or "", world.active_players)
