import logging
import secrets
from urllib.parse import quote

from fastapi import APIRouter, Cookie, Depends
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from auth import google as g
from auth.crypto import encrypt
from auth.session import COOKIE, STATE_COOKIE, get_current_user, set_session_cookie
from auth import linkedin as li
from config import settings
from db import get_db
from models.models import LinkedInConnection, User, utcnow

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)
LINKEDIN_STATE_COOKIE = "nexus_linkedin_state"
LINKEDIN_NONCE_COOKIE = "nexus_linkedin_nonce"


def _back(error: str) -> RedirectResponse:
    return RedirectResponse(f"{settings.FRONTEND_URL}/?error={quote(error)}")


@router.get("/google/login")
def google_login():
    if not settings.google_configured:
        return _back("google_not_configured")
    state = g.new_state()
    resp = RedirectResponse(g.build_auth_url(state))
    resp.set_cookie(
        STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=settings.FRONTEND_URL.startswith("https"),
        path="/",
    )
    return resp


@router.get("/google/callback")
def google_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    nexus_oauth_state: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if error:
        return _back(error)
    if not code or not state or state != nexus_oauth_state:
        return _back("invalid_state")
    try:
        tok = g.exchange_code(code)
        info = g.fetch_userinfo(tok["access_token"])
    except g.GoogleAuthError as e:
        return _back(f"oauth_failed: {str(e)[:120]}")

    granted = tok.get("scope", "")
    if "gmail.readonly" not in granted or "calendar.events" not in granted:
        return _back("missing_scopes")

    user = db.scalar(select(User).where(User.google_id == info["sub"]))
    if not user:
        user = User(google_id=info["sub"], email=info.get("email", ""))
        db.add(user)
    user.email = info.get("email", user.email)
    user.name = info.get("name") or user.email.split("@")[0]
    user.picture = info.get("picture", "")
    user.access_token_enc = encrypt(tok["access_token"])
    if tok.get("refresh_token"):  # Google only sends this on consent
        user.refresh_token_enc = encrypt(tok["refresh_token"])
    user.token_expiry = g.expiry_from(tok)
    user.is_demo = False
    db.commit()

    resp = RedirectResponse(f"{settings.FRONTEND_URL}/dashboard")
    # Explicitly remove any prior account session before issuing the new one.
    # This makes account switching deterministic when a demo cookie already exists.
    resp.delete_cookie(
        COOKIE,
        path="/",
        samesite="lax",
        secure=settings.FRONTEND_URL.startswith("https"),
    )
    set_session_cookie(resp, user.id)
    resp.delete_cookie(STATE_COOKIE, path="/")
    return resp


@router.get("/demo")
def demo_login(db: Session = Depends(get_db)):
    if not settings.DEMO_MODE:
        return _back("demo_disabled")
    user = db.scalar(select(User).where(User.google_id == "demo-user"))
    if not user:
        user = User(google_id="demo-user", email="hemant.demo@nexusai.dev", name="Hemant Saini", is_demo=True)
        db.add(user)
        db.commit()
    resp = RedirectResponse(f"{settings.FRONTEND_URL}/dashboard")
    set_session_cookie(resp, user.id)
    return resp


@router.post("/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/")
    return resp


def _linkedin_result(result: str) -> RedirectResponse:
    return RedirectResponse(f"{settings.FRONTEND_URL}/sources?linkedin={quote(result)}", status_code=303)


def _clear_linkedin_oauth_cookies(response: RedirectResponse) -> None:
    path = "/api/auth/linkedin"
    secure = settings.FRONTEND_URL.startswith("https")
    response.delete_cookie(LINKEDIN_STATE_COOKIE, path=path, secure=secure, samesite="lax")
    response.delete_cookie(LINKEDIN_NONCE_COOKIE, path=path, secure=secure, samesite="lax")


@router.get("/linkedin/connect")
def linkedin_connect(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        if not settings.linkedin_configured:
            return _linkedin_result("not_configured")
        if user.is_demo:
            return _linkedin_result("real_account_required")
        if not inspect(db.bind).has_table("linkedin_connections") or "headline" not in {
            column["name"] for column in inspect(db.bind).get_columns("linkedin_connections")
        }:
            return _linkedin_result("database_update_required")
        state, nonce = li.new_value(), li.new_value()
        response = RedirectResponse(li.build_authorization_url(state, nonce), status_code=302)
        cookie_options = {
            "max_age": 600,
            "httponly": True,
            "secure": settings.FRONTEND_URL.startswith("https"),
            "samesite": "lax",
            "path": "/api/auth/linkedin",
        }
        response.set_cookie(LINKEDIN_STATE_COOKIE, state, **cookie_options)
        response.set_cookie(LINKEDIN_NONCE_COOKIE, nonce, **cookie_options)
        return response
    except Exception as exc:
        db.rollback()
        # Keep DB details and OAuth state out of the browser while avoiding an
        # opaque 500 when a concurrent local sync temporarily locks SQLite.
        logger.warning("LinkedIn connect could not start for user %s (%s)", user.id, type(exc).__name__)
        return _linkedin_result("linkedin_connect_unavailable")


@router.get("/linkedin/callback")
def linkedin_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    nexus_linkedin_state: str | None = Cookie(default=None, alias=LINKEDIN_STATE_COOKIE),
    nexus_linkedin_nonce: str | None = Cookie(default=None, alias=LINKEDIN_NONCE_COOKIE),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not state or not nexus_linkedin_state or not secrets.compare_digest(state, nexus_linkedin_state):
        response = _linkedin_result("invalid_state")
        _clear_linkedin_oauth_cookies(response)
        return response
    if error:
        response = _linkedin_result("authorization_cancelled")
        _clear_linkedin_oauth_cookies(response)
        return response
    if user.is_demo:
        response = _linkedin_result("real_account_required")
        _clear_linkedin_oauth_cookies(response)
        return response
    if not code or not nexus_linkedin_nonce:
        response = _linkedin_result("authorization_failed")
        _clear_linkedin_oauth_cookies(response)
        return response
    if not inspect(db.bind).has_table("linkedin_connections") or "headline" not in {
        column["name"] for column in inspect(db.bind).get_columns("linkedin_connections")
    }:
        response = _linkedin_result("database_update_required")
        _clear_linkedin_oauth_cookies(response)
        return response
    try:
        token = li.exchange_code(code)
        claims = li.validate_id_token(token["id_token"], nexus_linkedin_nonce)
        profile = li.fetch_userinfo(token["access_token"], expected_sub=claims["sub"])
        connection = db.scalar(select(LinkedInConnection).where(LinkedInConnection.user_id == user.id))
        if connection is None:
            connection = LinkedInConnection(user_id=user.id, linkedin_sub=claims["sub"])
            db.add(connection)
        elif connection.linkedin_sub != claims["sub"]:
            # Keep one LinkedIn identity per Nexus user; the new authorized
            # account replaces the previous one only after full validation.
            connection.linkedin_sub = claims["sub"]
        li.save_token(connection, token)
        li.apply_profile(connection, profile)
        connection.status = "connected"
        connection.last_error_code = ""
        connection.last_attempt_at = utcnow()
        connection.last_synced_at = connection.last_attempt_at
        granted = token.get("scope")
        if isinstance(granted, str) and granted.strip():
            connection.scopes = granted[:255]
        db.commit()
        result = "connected"
    except li.LinkedInAuthError as exc:
        db.rollback()
        cause_type = type(exc.__cause__).__name__ if exc.__cause__ else "none"
        logger.warning("LinkedIn connection failed for user %s (%s; cause=%s)", user.id, exc.code, cause_type)
        result = exc.code
    except Exception as exc:
        db.rollback()
        logger.warning("LinkedIn connection could not be saved for user %s (%s)", user.id, type(exc).__name__)
        result = "linkedin_connection_failed"
    response = _linkedin_result(result)
    _clear_linkedin_oauth_cookies(response)
    return response
