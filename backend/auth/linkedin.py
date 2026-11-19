"""LinkedIn Sign In with OpenID Connect for the web application."""
from __future__ import annotations

import secrets
from datetime import timedelta
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

from config import settings
from models.models import LinkedInConnection, utcnow

DISCOVERY_URL = "https://www.linkedin.com/oauth/.well-known/openid-configuration"
# LinkedIn's live discovery document currently reports the /oauth issuer,
# while its OIDC guide also documents the host-root issuer. Both are official
# LinkedIn issuer values; accept either while still validating the signed
# token's issuer, audience, expiry, and nonce.
EXPECTED_ISSUERS = frozenset({
    "https://www.linkedin.com/oauth",
    "https://www.linkedin.com",
})
AUTHORIZATION_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
REQUESTED_SCOPES = ("openid", "profile", "email")


class LinkedInAuthError(Exception):
    def __init__(self, code: str, *, status_code: int | None = None):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def new_value() -> str:
    return secrets.token_urlsafe(32)


def build_authorization_url(state: str, nonce: str | None = None) -> str:
    query = urlencode({
        "response_type": "code",
        "client_id": settings.LINKEDIN_CLIENT_ID,
        "redirect_uri": settings.LINKEDIN_REDIRECT_URI,
        "scope": " ".join(REQUESTED_SCOPES),
        "state": state,
    })
    return f"{AUTHORIZATION_URL}?{query}"


def _provider_error(status: int) -> LinkedInAuthError:
    code = (
        "linkedin_reconnect_required" if status == 401 else
        "linkedin_permission_denied" if status == 403 else
        "linkedin_rate_limited" if status == 429 else
        "linkedin_temporarily_unavailable" if status >= 500 else
        "linkedin_request_failed"
    )
    return LinkedInAuthError(code, status_code=status)


def exchange_code(code: str) -> dict:
    if not settings.linkedin_configured:
        raise LinkedInAuthError("linkedin_not_configured")
    try:
        response = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.LINKEDIN_REDIRECT_URI,
                "client_id": settings.LINKEDIN_CLIENT_ID,
                "client_secret": settings.LINKEDIN_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=httpx.Timeout(20, connect=5),
        )
    except httpx.HTTPError as exc:
        raise LinkedInAuthError("linkedin_temporarily_unavailable") from exc
    if response.status_code != 200:
        raise _provider_error(response.status_code)
    try:
        token = response.json()
    except ValueError as exc:
        raise LinkedInAuthError("linkedin_invalid_token_response") from exc
    if not isinstance(token, dict) or not token.get("access_token") or not token.get("id_token"):
        raise LinkedInAuthError("linkedin_invalid_token_response")
    return token


def _metadata() -> dict:
    try:
        response = httpx.get(DISCOVERY_URL, timeout=httpx.Timeout(12, connect=4))
        response.raise_for_status()
        metadata = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LinkedInAuthError("linkedin_identity_service_unavailable") from exc
    if not isinstance(metadata, dict) or metadata.get("issuer") not in EXPECTED_ISSUERS:
        raise LinkedInAuthError("linkedin_identity_configuration_invalid")
    if metadata.get("authorization_endpoint") != AUTHORIZATION_URL or metadata.get("token_endpoint") != TOKEN_URL:
        raise LinkedInAuthError("linkedin_identity_configuration_invalid")
    if metadata.get("userinfo_endpoint") != USERINFO_URL:
        raise LinkedInAuthError("linkedin_identity_configuration_invalid")
    if metadata.get("jwks_uri") != "https://www.linkedin.com/oauth/openid/jwks":
        raise LinkedInAuthError("linkedin_identity_configuration_invalid")
    return metadata


def validate_id_token(id_token: str, nonce: str | None = None) -> dict:
    metadata = _metadata()
    try:
        header = jwt.get_unverified_header(id_token)
        if header.get("alg") != "RS256":
            raise LinkedInAuthError("linkedin_id_token_invalid")
        key = PyJWKClient(metadata["jwks_uri"]).get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            key.key,
            algorithms=["RS256"],
            audience=settings.LINKEDIN_CLIENT_ID,
            # See EXPECTED_ISSUERS: LinkedIn's published guide and live
            # discovery currently differ on this claim's exact URI.
            issuer=None,
            # LinkedIn's OIDC discovery document does not advertise `nonce` in
            # claims_supported, and its returned ID token omits it. The OAuth
            # state cookie remains the callback CSRF defense; retain all signed
            # token, issuer, audience, expiry, and subject checks here.
            options={"require": ["iss", "aud", "exp", "iat", "sub"]},
            leeway=60,
        )
    except jwt.ExpiredSignatureError as exc:
        raise LinkedInAuthError("linkedin_id_token_expired") from exc
    except jwt.InvalidAudienceError as exc:
        # A frequent local setup issue is using a client ID from another LinkedIn app.
        # Keep the detail useful without ever logging or returning token contents.
        raise LinkedInAuthError("linkedin_app_mismatch") from exc
    except jwt.InvalidSignatureError as exc:
        raise LinkedInAuthError("linkedin_id_token_signature_invalid") from exc
    except jwt.MissingRequiredClaimError as exc:
        raise LinkedInAuthError("linkedin_id_token_claims_missing") from exc
    except jwt.PyJWKClientError as exc:
        raise LinkedInAuthError("linkedin_signing_key_unavailable") from exc
    except (jwt.PyJWTError, httpx.HTTPError, KeyError, ValueError) as exc:
        raise LinkedInAuthError("linkedin_id_token_invalid") from exc
    if claims.get("iss") not in EXPECTED_ISSUERS:
        raise LinkedInAuthError("linkedin_id_token_invalid")
    if nonce and claims.get("nonce") and not secrets.compare_digest(str(claims["nonce"]), nonce):
        raise LinkedInAuthError("linkedin_nonce_mismatch")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise LinkedInAuthError("linkedin_id_token_invalid")
    return claims


def fetch_userinfo(access_token: str, *, expected_sub: str) -> dict:
    try:
        response = httpx.get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=httpx.Timeout(20, connect=5),
        )
    except httpx.HTTPError as exc:
        raise LinkedInAuthError("linkedin_temporarily_unavailable") from exc
    if response.status_code != 200:
        raise _provider_error(response.status_code)
    try:
        profile = response.json()
    except ValueError as exc:
        raise LinkedInAuthError("linkedin_invalid_profile_response") from exc
    if not isinstance(profile, dict) or not isinstance(profile.get("sub"), str):
        raise LinkedInAuthError("linkedin_invalid_profile_response")
    if not secrets.compare_digest(profile["sub"], expected_sub):
        raise LinkedInAuthError("linkedin_subject_mismatch")
    return profile


def save_token(connection: LinkedInConnection, token: dict) -> None:
    from auth.crypto import encrypt

    now = utcnow()
    connection.access_token_enc = encrypt(token["access_token"])
    connection.access_token_expires_at = now + timedelta(seconds=max(1, int(token.get("expires_in", 0))))
    # LinkedIn issues programmatic refresh tokens only for eligible approved
    # partners. Keep an existing one when a response does not rotate it.
    if token.get("refresh_token"):
        connection.refresh_token_enc = encrypt(token["refresh_token"])
        refresh_lifetime = token.get("refresh_token_expires_in")
        if refresh_lifetime:
            connection.refresh_token_expires_at = now + timedelta(seconds=max(1, int(refresh_lifetime)))


def refresh_access_token(connection: LinkedInConnection) -> bool:
    from auth.crypto import decrypt, encrypt

    refresh_token = decrypt(connection.refresh_token_enc)
    if not refresh_token or not settings.linkedin_configured:
        return False
    if connection.refresh_token_expires_at and connection.refresh_token_expires_at <= utcnow():
        return False
    try:
        response = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.LINKEDIN_CLIENT_ID,
                "client_secret": settings.LINKEDIN_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=httpx.Timeout(20, connect=5),
        )
    except httpx.HTTPError:
        return False
    if response.status_code != 200:
        return False
    try:
        token = response.json()
        if not isinstance(token, dict) or not token.get("access_token"):
            return False
        connection.access_token_enc = encrypt(token["access_token"])
        connection.access_token_expires_at = utcnow() + timedelta(
            seconds=max(1, int(token.get("expires_in", 0)))
        )
        if token.get("refresh_token"):
            connection.refresh_token_enc = encrypt(token["refresh_token"])
        if token.get("refresh_token_expires_in"):
            connection.refresh_token_expires_at = utcnow() + timedelta(
                seconds=max(1, int(token["refresh_token_expires_in"]))
            )
        return True
    except (ValueError, TypeError):
        return False


def apply_profile(connection: LinkedInConnection, profile: dict) -> None:
    # Use only claims returned by LinkedIn's authorized OIDC userinfo response.
    connection.linkedin_sub = profile["sub"]
    connection.full_name = _safe_text(profile.get("name"), 255)
    connection.given_name = _safe_text(profile.get("given_name"), 128)
    connection.family_name = _safe_text(profile.get("family_name"), 128)
    headline = profile.get("headline") or profile.get("localizedHeadline")
    if isinstance(headline, dict):
        localized = headline.get("localized")
        preferred = headline.get("preferredLocale") or {}
        locale_key = f"{preferred.get('language', '')}_{preferred.get('country', '')}".strip("_")
        headline = localized.get(locale_key) if isinstance(localized, dict) else None
        if not headline and isinstance(localized, dict):
            headline = next(iter(localized.values()), "")
    connection.headline = _safe_text(headline, 512)
    connection.email = _safe_text(profile.get("email"), 255)
    verified = profile.get("email_verified")
    connection.email_verified = verified if isinstance(verified, bool) else None
    picture = _safe_text(profile.get("picture"), 2048)
    connection.picture_url = picture if picture.startswith("https://") else ""
    connection.locale = _safe_text(profile.get("locale"), 64)


def _safe_text(value, max_length: int) -> str:
    return value.strip()[:max_length] if isinstance(value, str) else ""
