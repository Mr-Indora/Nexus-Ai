"""Google Calendar event reads, user-initiated writes, and Meet link detection."""
import re
from datetime import datetime, timedelta, timezone

from integrations.google_api import GoogleClient

URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
MEET_RE = re.compile(r"https://meet\.google\.com/[a-z0-9\-]+(?:\?[^\s\"<>]*)?", re.I)


def _utc_naive(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _parse_when(obj: dict) -> tuple[datetime, bool]:
    if "dateTime" in obj:
        return _utc_naive(datetime.fromisoformat(obj["dateTime"].replace("Z", "+00:00"))), False
    return datetime.fromisoformat(obj["date"]), True  # all-day (date only)


def find_meet_link(ev: dict) -> str:
    if ev.get("hangoutLink"):
        return ev["hangoutLink"]
    for ep in (ev.get("conferenceData") or {}).get("entryPoints", []) or []:
        if ep.get("entryPointType") == "video" and "meet.google.com" in ep.get("uri", ""):
            return ep["uri"]
    m = MEET_RE.search(" ".join([ev.get("description") or "", ev.get("location") or ""]))
    return m.group(0) if m else ""


def parse_event(ev: dict) -> dict | None:
    if ev.get("status") == "cancelled" or "start" not in ev:
        return None
    start, all_day = _parse_when(ev["start"])
    end, _ = _parse_when(ev["end"]) if "end" in ev else (None, all_day)
    return {
        "google_id": ev["id"],
        "title": ev.get("summary") or "(No title)",
        "start": start,
        "end": end,
        "all_day": all_day,
        "location": ev.get("location") or "",
        "meet_link": find_meet_link(ev),
        "description": ev.get("description") or "",
    }


def fetch_event_changes(
    client: GoogleClient, days_back: int = 7, days_ahead: int = 45,
) -> tuple[list[dict], list[str]]:
    now = datetime.now(timezone.utc)
    params = {
        "timeMin": (now - timedelta(days=days_back)).isoformat(),
        "timeMax": (now + timedelta(days=days_ahead)).isoformat(),
        "singleEvents": "true",
        "showDeleted": "true",
        "orderBy": "startTime",
        "maxResults": 250,
        "conferenceDataVersion": 1,
    }
    items: list[dict] = []
    page_token: str | None = None
    while True:
        page_params = {**params}
        if page_token:
            page_params["pageToken"] = page_token
        page = client.get(URL, page_params)
        items.extend(page.get("items", []))
        page_token = page.get("nextPageToken")
        if not page_token:
            break
    cancelled = [item["id"] for item in items if item.get("status") == "cancelled" and item.get("id")]
    events = [event for item in items if (event := parse_event(item))]
    return events, cancelled


def fetch_events(client: GoogleClient, days_back: int = 7, days_ahead: int = 45) -> list[dict]:
    events, _ = fetch_event_changes(client, days_back, days_ahead)
    return events
