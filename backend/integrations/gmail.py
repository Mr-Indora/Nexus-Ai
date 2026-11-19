"""Gmail (read-only): fetch recent inbox emails and flatten to plain text."""
import base64
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import getaddresses
from html.parser import HTMLParser

from integrations.google_api import GoogleAPIError, GoogleClient

BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_BODY = 4000
MAX_HISTORY_PAGES_PER_SYNC = 5


class _Strip(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        if tag in ("br", "p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _Strip()
    p.feed(html)
    return clean_text("".join(p.parts))


def clean_text(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def extract_body(payload: dict) -> str:
    """Prefer text/plain; fall back to stripped text/html."""
    plain, html = [], []

    def walk(part):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mime == "text/plain":
            plain.append(_b64(data))
        elif data and mime == "text/html":
            html.append(_b64(data))
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    text = clean_text("\n".join(plain)) if plain else html_to_text("\n".join(html))
    return text[:MAX_BODY]


def parse_message(msg: dict) -> dict:
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    received = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=timezone.utc).replace(tzinfo=None)
    recipients = [
        address
        for _, address in getaddresses([
            value for value in (
                headers.get("to", ""), headers.get("cc", ""), headers.get("bcc", "")
            ) if value
        ])
        if address
    ]
    return {
        "gmail_id": msg["id"],
        "thread_id": msg.get("threadId") or None,
        "sender": headers.get("from", ""),
        "recipients": recipients,
        "labels": list(msg.get("labelIds") or []),
        "subject": headers.get("subject", "(no subject)"),
        "snippet": msg.get("snippet", ""),
        "body": extract_body(msg.get("payload", {})),
        "received_at": received,
    }


def _fetch_email_page(
    client: GoogleClient,
    *,
    max_results: int,
    days: int,
    page_token: str | None = None,
) -> tuple[list[dict], str | None]:
    """Fetch and parse one bounded page from received mail in any category/folder."""
    lookback = f"newer_than:{days}d " if days else ""
    q = f"{lookback}-in:spam -in:trash -in:sent"
    ids: list[str] = []
    seen: set[str] = set()
    next_page_token = None
    while len(ids) < max_results:
        params = {"q": q, "maxResults": min(100, max_results - len(ids))}
        if page_token:
            params["pageToken"] = page_token
        listing = client.get(f"{BASE}/messages", params)
        for message in listing.get("messages", []):
            message_id = message["id"]
            if message_id not in seen:
                ids.append(message_id)
                seen.add(message_id)
                if len(ids) == max_results:
                    break
        next_page_token = listing.get("nextPageToken")
        if not next_page_token or not listing.get("messages"):
            break
        # Continue across Gmail's 100-item page size until this sync's own limit.
        page_token = next_page_token

    def one(mid: str) -> dict:
        return parse_message(client.get(f"{BASE}/messages/{mid}", {"format": "full"}))

    with ThreadPoolExecutor(max_workers=5) as pool:
        messages = sorted(pool.map(one, ids), key=lambda message: message["received_at"], reverse=True)
    return messages, next_page_token


def fetch_recent_emails(client: GoogleClient, max_results: int = 500, days: int = 0) -> list[dict]:
    """Fetch up to max_results received emails, including archived and bulk categories."""
    all_messages: list[dict] = []
    page_token = None
    while len(all_messages) < max_results:
        messages, next_page_token = _fetch_email_page(
            client, max_results=min(100, max_results - len(all_messages)),
            days=days, page_token=page_token,
        )
        all_messages.extend(messages)
        if not next_page_token or not messages:
            break
        page_token = next_page_token
    return sorted(all_messages, key=lambda message: message["received_at"], reverse=True)


def fetch_mailbox_updates(
    client: GoogleClient,
    start_history_id: str | None = None,
    *,
    max_results: int = 500,
    days: int = 0,
) -> dict:
    """Fetch a safe initial window or changes since Gmail's stored history cursor."""
    profile = client.get(f"{BASE}/profile")
    latest_history_id = str(profile.get("historyId") or "") or None
    page_token = None
    backfill_anchor = None
    versioned_history_cursor = False
    if start_history_id:
        try:
            decoded_cursor = json.loads(start_history_id)
        except (TypeError, json.JSONDecodeError):
            decoded_cursor = None
        if isinstance(decoded_cursor, dict) and decoded_cursor.get("version") == 2 and decoded_cursor.get("phase") == "backfill":
            backfill_anchor = str(decoded_cursor.get("startHistoryId") or "") or latest_history_id
            page_token = decoded_cursor.get("pageToken")
        elif isinstance(decoded_cursor, dict) and decoded_cursor.get("version") == 2 and decoded_cursor.get("phase") == "history":
            versioned_history_cursor = True
            start_history_id = str(decoded_cursor.get("startHistoryId") or "") or None
            page_token = decoded_cursor.get("pageToken")
        elif isinstance(decoded_cursor, dict) and decoded_cursor.get("version") == 1:
            start_history_id = str(decoded_cursor.get("startHistoryId") or "") or None
            page_token = decoded_cursor.get("pageToken")
        else:
            # Older app versions stored the Gmail history id after importing only
            # a small recent window. Backfill those existing accounts as well.
            backfill_anchor = latest_history_id
    if not start_history_id or backfill_anchor:
        anchor = backfill_anchor or latest_history_id
        messages, next_page_token = _fetch_email_page(
            client, max_results=max_results, days=days, page_token=page_token,
        )
        cursor = (
            json.dumps({"version": 2, "phase": "backfill", "startHistoryId": anchor,
                        "pageToken": next_page_token}, separators=(",", ":"))
            if next_page_token else json.dumps(
                {"version": 2, "phase": "history", "startHistoryId": anchor}, separators=(",", ":")
            )
        )
        return {"messages": messages, "deleted_ids": [], "cursor": cursor,
                "reset": not bool(backfill_anchor), "pending_history": bool(next_page_token)}

    changed_ids: list[str] = []
    deleted_ids: set[str] = set()
    next_cursor = latest_history_id
    pending_history = False
    try:
        for page_index in range(MAX_HISTORY_PAGES_PER_SYNC):
            params = {"startHistoryId": start_history_id, "maxResults": 100}
            if page_token:
                params["pageToken"] = page_token
            page = client.get(f"{BASE}/history", params)
            latest_history_id = str(page.get("historyId") or latest_history_id or "") or None
            for history in page.get("history", []):
                for group in ("messagesAdded", "messagesUpdated", "labelsAdded", "labelsRemoved"):
                    for change in history.get(group, []) or []:
                        message_id = (change.get("message") or {}).get("id")
                        if message_id and message_id not in deleted_ids and message_id not in changed_ids:
                            changed_ids.append(message_id)
                for change in history.get("messagesDeleted", []) or []:
                    message_id = (change.get("message") or {}).get("id")
                    if message_id:
                        deleted_ids.add(message_id)
            page_token = page.get("nextPageToken")
            if not page_token:
                next_cursor = latest_history_id
                break
            if page_index == MAX_HISTORY_PAGES_PER_SYNC - 1:
                next_cursor = json.dumps({
                    "version": 2 if versioned_history_cursor else 1,
                    **({"phase": "history"} if versioned_history_cursor else {}),
                    "startHistoryId": start_history_id,
                    "pageToken": page_token,
                }, separators=(",", ":"))
                pending_history = True
    except GoogleAPIError as exc:
        if exc.status != 404:
            raise
        messages, next_page_token = _fetch_email_page(
            client, max_results=max_results, days=days,
        )
        cursor = (
            json.dumps({"version": 2, "phase": "backfill", "startHistoryId": latest_history_id,
                        "pageToken": next_page_token}, separators=(",", ":"))
            if next_page_token else json.dumps(
                {"version": 2, "phase": "history", "startHistoryId": latest_history_id}, separators=(",", ":")
            )
        )
        return {"messages": messages, "deleted_ids": [], "cursor": cursor, "reset": True,
                "pending_history": bool(next_page_token)}

    messages: list[dict] = []
    missing: set[str] = set()

    def one(message_id: str):
        try:
            return parse_message(client.get(f"{BASE}/messages/{message_id}", {"format": "full"}))
        except GoogleAPIError as exc:
            if exc.status == 404:
                return None
            raise

    with ThreadPoolExecutor(max_workers=5) as pool:
        fetched = list(pool.map(one, changed_ids))
    for message_id, message in zip(changed_ids, fetched):
        if message is None:
            missing.add(message_id)
        else:
            messages.append(message)
    if versioned_history_cursor and not pending_history:
        next_cursor = json.dumps(
            {"version": 2, "phase": "history", "startHistoryId": latest_history_id},
            separators=(",", ":"),
        )
    return {
        "messages": sorted(messages, key=lambda message: message["received_at"], reverse=True),
        "deleted_ids": sorted(deleted_ids | missing),
        "cursor": next_cursor,
        "reset": False,
        "pending_history": pending_history,
    }
