"""Owner-scoped LinkedIn profile connection endpoints."""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from auth import linkedin as li
from auth.crypto import decrypt
from auth.session import get_current_user
from config import settings
from db import get_db
from models.models import LinkedInConnection, User, utcnow

router = APIRouter(prefix="/api/linkedin", tags=["linkedin"])
logger = logging.getLogger(__name__)


def _public_status(connection: LinkedInConnection | None) -> dict:
    if not connection:
        return {
            "configured": settings.linkedin_configured,
            "connected": False,
            "status": "disconnected" if settings.linkedin_configured else "not_configured",
            "lastAttemptAt": None,
            "lastSyncedAt": None,
            "lastErrorCode": None,
            "profile": None,
        }
    return {
        "configured": settings.linkedin_configured,
        "connected": connection.status == "connected",
        "status": connection.status,
        "lastAttemptAt": connection.last_attempt_at.isoformat(timespec="seconds") + "Z" if connection.last_attempt_at else None,
        "lastSyncedAt": connection.last_synced_at.isoformat(timespec="seconds") + "Z" if connection.last_synced_at else None,
        "lastErrorCode": connection.last_error_code or None,
        "profile": {
            "name": connection.full_name,
            "givenName": connection.given_name,
            "familyName": connection.family_name,
            "headline": connection.headline or None,
            "email": connection.email or None,
            "emailVerified": connection.email_verified,
            "pictureUrl": connection.picture_url or None,
            "locale": connection.locale or None,
        },
    }


def _store_error(db: Session, connection: LinkedInConnection, code: str) -> dict:
    connection.status = "reconnect_required" if code == "linkedin_reconnect_required" else "sync_attention"
    connection.last_error_code = code
    db.commit()
    return _public_status(connection)


@router.get("/status")
def linkedin_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not inspect(db.bind).has_table("linkedin_connections") or "headline" not in {
        column["name"] for column in inspect(db.bind).get_columns("linkedin_connections")
    }:
        return {
            "configured": settings.linkedin_configured,
            "connected": False,
            "status": "database_update_required",
            "lastAttemptAt": None,
            "lastSyncedAt": None,
            "lastErrorCode": "database_update_required",
            "profile": None,
        }
    connection = db.scalar(select(LinkedInConnection).where(LinkedInConnection.user_id == user.id))
    return _public_status(connection)


@router.post("/sync")
def sync_linkedin(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not inspect(db.bind).has_table("linkedin_connections") or "headline" not in {
        column["name"] for column in inspect(db.bind).get_columns("linkedin_connections")
    }:
        raise HTTPException(503, "Update the workspace database before connecting LinkedIn.")
    connection = db.scalar(select(LinkedInConnection).where(LinkedInConnection.user_id == user.id))
    if connection is None:
        raise HTTPException(409, "Connect LinkedIn before syncing your profile.")
    if not settings.linkedin_configured:
        return _store_error(db, connection, "linkedin_not_configured")

    connection.last_attempt_at = utcnow()
    connection.status = "syncing"
    db.commit()
    try:
        if connection.access_token_expires_at and connection.access_token_expires_at <= utcnow():
            if not li.refresh_access_token(connection):
                return _store_error(db, connection, "linkedin_reconnect_required")
        access_token = decrypt(connection.access_token_enc)
        if not access_token:
            return _store_error(db, connection, "linkedin_reconnect_required")
        try:
            profile = li.fetch_userinfo(access_token, expected_sub=connection.linkedin_sub)
        except li.LinkedInAuthError as exc:
            if exc.status_code == 401 and li.refresh_access_token(connection):
                access_token = decrypt(connection.access_token_enc)
                profile = li.fetch_userinfo(access_token, expected_sub=connection.linkedin_sub)
            else:
                raise
        li.apply_profile(connection, profile)
        connection.status = "connected"
        connection.last_error_code = ""
        connection.last_synced_at = utcnow()
        db.commit()
        return _public_status(connection)
    except li.LinkedInAuthError as exc:
        db.rollback()
        current = db.scalar(select(LinkedInConnection).where(LinkedInConnection.user_id == user.id))
        if current is None:
            raise HTTPException(404, "LinkedIn connection was removed.") from exc
        logger.warning("LinkedIn profile sync failed for user %s (%s)", user.id, exc.code)
        return _store_error(db, current, exc.code)
    except Exception as exc:
        db.rollback()
        current = db.scalar(select(LinkedInConnection).where(LinkedInConnection.user_id == user.id))
        if current is None:
            raise HTTPException(404, "LinkedIn connection was removed.") from exc
        logger.warning("LinkedIn profile sync failed for user %s (%s)", user.id, type(exc).__name__)
        return _store_error(db, current, "linkedin_sync_failed")


@router.delete("/connection")
def disconnect_linkedin(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not inspect(db.bind).has_table("linkedin_connections"):
        return {"ok": True}
    connection = db.scalar(select(LinkedInConnection).where(LinkedInConnection.user_id == user.id))
    if connection:
        db.delete(connection)
        db.commit()
    return {"ok": True}
