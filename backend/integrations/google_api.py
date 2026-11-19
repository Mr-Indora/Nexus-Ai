"""Authenticated Google REST client with token refresh and bounded retries."""
import time

import httpx
from sqlalchemy.orm import Session

from auth.crypto import decrypt, encrypt
from auth.google import GoogleAuthError, valid_access_token
from models.models import User


class GoogleAPIError(Exception):
    def __init__(self, status: int, message: str, reason: str | None = None):
        super().__init__(f"Google API {status}: {message}")
        self.status = status
        self.reason = reason


class _RetryingHTTPClient:
    RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

    def __init__(self):
        self._client = httpx.Client(timeout=25)

    def get(self, url: str, params: dict | None = None, **kwargs) -> httpx.Response:
        return self.request("GET", url, params=params, **kwargs)

    def request(self, method: str, url: str, params: dict | None = None, **kwargs) -> httpx.Response:
        # Calendar event creation is a POST; never retry it automatically because
        # a timed-out response can mean Google already created the event.
        max_attempts = 1 if method.upper() == "POST" else 3
        for attempt in range(max_attempts):
            try:
                response = self._client.request(method, url, params=params, **kwargs)
            except httpx.TransportError:
                if attempt == max_attempts - 1:
                    raise
            else:
                if response.status_code not in self.RETRYABLE_STATUS_CODES or attempt == max_attempts - 1:
                    return response
                try:
                    retry_after = min(float(response.headers.get("Retry-After") or 0), 2.0)
                except (TypeError, ValueError):
                    retry_after = 0
                time.sleep(max(retry_after, 0.25 * (2 ** attempt)))
                continue
            time.sleep(0.25 * (2 ** attempt))
        raise RuntimeError("Google API retry loop exhausted unexpectedly")

    def close(self) -> None:
        self._client.close()


class GoogleClient:
    def __init__(self, user: User, db: Session):
        self.user, self.db = user, db
        self.token = valid_access_token(user, db, decrypt, encrypt)
        self.http = _RetryingHTTPClient()

    def _request(self, method: str, url: str, params: dict | None = None, json: dict | None = None) -> dict:
        r = self.http.request(method, url, params=params, json=json,
                              headers={"Authorization": f"Bearer {self.token}"})
        if r.status_code == 401:  # token revoked/expired early -> force one refresh
            self.user.token_expiry = None
            self.db.commit()
            try:
                self.token = valid_access_token(self.user, self.db, decrypt, encrypt)
            except GoogleAuthError as e:
                raise GoogleAPIError(401, str(e))
            r = self.http.request(method, url, params=params, json=json,
                                  headers={"Authorization": f"Bearer {self.token}"})
        if r.status_code not in {200, 201}:
            reason = None
            message = "Google API request failed"
            try:
                error = r.json().get("error", {})
                message = str(error.get("message") or message)[:240]
                errors = error.get("errors") or []
                if errors and isinstance(errors[0], dict):
                    reason = str(errors[0].get("reason") or "")[:80] or None
                if reason is None:
                    details = error.get("details") or []
                    reason = next((str(item.get("reason"))[:80] for item in details
                                   if isinstance(item, dict) and item.get("reason")), None)
            except (ValueError, AttributeError, TypeError):
                pass
            raise GoogleAPIError(r.status_code, message, reason)
        return r.json()

    def get(self, url: str, params: dict | None = None) -> dict:
        return self._request("GET", url, params=params)

    def post(self, url: str, payload: dict) -> dict:
        return self._request("POST", url, json=payload)

    def patch(self, url: str, payload: dict) -> dict:
        return self._request("PATCH", url, json=payload)

    def close(self):
        self.http.close()
