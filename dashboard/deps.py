from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_token_subject
from app.crud import get_user_by_username


def get_current_user(request: Request, db: Session = Depends(get_db)):
    token = request.session.get("token")
    if not token:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/dashboard/login"})
    username = get_token_subject(token)
    if username is None:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/dashboard/login"})
    user = get_user_by_username(db, username)
    if user is None:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/dashboard/login"})
    return user
