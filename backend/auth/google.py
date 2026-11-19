"""Google OAuth 2.0 (authorization-code flow) using plain httpx."""
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import httpx

from config import settings
from models.models import User, utcnow

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


class GoogleAuthError(Exception):
    pass


def new_state() -> str:
    return secrets.token_urlsafe(24)


def build_auth_url(state: str) -> str:
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(settings.SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }

    return f"{AUTH_URL}?{urlencode(params)}"


def _token_request(data: dict) -> dict:
    try:
        r = httpx.post(TOKEN_URL, data=data, timeout=20)
    except httpx.HTTPError as e:
        raise GoogleAuthError("Google token endpoint is temporarily unreachable") from e
    if r.status_code != 200:
        raise GoogleAuthError(f"Google token endpoint returned HTTP {r.status_code}")
    return r.json()


def exchange_code(code: str) -> dict:
    return _token_request(
        {
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        }
    )


def refresh_access_token(refresh_token: str) -> dict:
    return _token_request(
        {
            "refresh_token": refresh_token,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "grant_type": "refresh_token",
        }
    )


def fetch_userinfo(access_token: str) -> dict:
    r = httpx.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=20)
    if r.status_code != 200:
        raise GoogleAuthError(f"Google user-info endpoint returned HTTP {r.status_code}")
    return r.json()


def expiry_from(token_json: dict):
    return utcnow() + timedelta(seconds=int(token_json.get("expires_in", 3600)) - 60)


def valid_access_token(user: User, db, decrypt, encrypt) -> str:
    """Return a usable access token, refreshing (and persisting) it when expired."""
    if user.token_expiry and user.token_expiry > utcnow() and user.access_token_enc:
        return decrypt(user.access_token_enc)
    refresh = decrypt(user.refresh_token_enc)
    if not refresh:
        raise GoogleAuthError("No refresh token stored. Please log in with Google again.")
    tok = refresh_access_token(refresh)
    user.access_token_enc = encrypt(tok["access_token"])
    user.token_expiry = expiry_from(tok)
    db.commit()
    return tok["access_token"]
