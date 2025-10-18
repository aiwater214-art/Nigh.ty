from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.core.database import get_db
from app.models import User as UserModel
from app.schemas import User, UserWithStats

router = APIRouter()


@router.get("/me", response_model=UserWithStats)
def read_users_me(current_user=Depends(get_current_active_user)):
    return current_user


@router.get("/", response_model=list[User])
def list_users(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_active_user),
):
    return db.query(UserModel).all()
