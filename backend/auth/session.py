"""Cookie session: signed JWT in an httpOnly cookie."""
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.orm import Session

from config import settings
from db import get_db
from models.models import User

COOKIE = "nexus_session"
STATE_COOKIE = "nexus_oauth_state"


def create_session_token(user_id: int) -> str:
    exp = datetime.now(timezone.utc) + timedelta(days=settings.SESSION_DAYS)
    return jwt.encode({"sub": str(user_id), "exp": exp}, settings.SECRET_KEY, algorithm="HS256")


def set_session_cookie(response, user_id: int) -> None:
    response.set_cookie(
        COOKIE,
        create_session_token(user_id),
        max_age=settings.SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=settings.FRONTEND_URL.startswith("https"),
        path="/",
    )


def get_current_user(
    nexus_session: str | None = Cookie(default=None), db: Session = Depends(get_db)
) -> User:
    if not nexus_session:
        raise HTTPException(401, "Not authenticated")
    try:
        data = jwt.decode(nexus_session, settings.SECRET_KEY, algorithms=["HS256"])
        user = db.get(User, int(data["sub"]))
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(401, "Invalid session") from exc
    if not user:
        raise HTTPException(401, "User not found")
    return user
