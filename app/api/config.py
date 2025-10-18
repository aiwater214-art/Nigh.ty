from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.crud import get_gameplay_config
from app.schemas import GameplayConfig

router = APIRouter()


@router.get("/config", response_model=GameplayConfig)
def read_config(db: Session = Depends(get_db)):
    return get_gameplay_config(db)

