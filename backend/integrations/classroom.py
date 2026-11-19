"""Bounded read-only Google Classroom coursework and announcement connector."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, TYPE_CHECKING
from urllib.parse import quote, urlparse

if TYPE_CHECKING:
    from integrations.google_api import GoogleClient

CLASSROOM_API = "https://classroom.googleapis.com/v1"
MAX_COURSES = 8
MAX_PAGE_SIZE = 25
MAX_ITEMS_PER_COURSE = 50


def _pages(client: Any, url: str, *, params: dict, rows_key: str) -> list[dict]:
    rows: list[dict] = []
    page_token = None
    for _ in range(2):
        query = {**params, "pageSize": MAX_PAGE_SIZE}
        if page_token:
            query["pageToken"] = page_token
        payload = client.get(url, params=query)
        rows.extend(item for item in payload.get(rows_key, []) if isinstance(item, dict))
        page_token = payload.get("nextPageToken")
        if not page_token or len(rows) >= MAX_ITEMS_PER_COURSE:
            break
    return rows[:MAX_ITEMS_PER_COURSE]


def fetch_classroom_items(client: Any) -> list[dict]:
    """Fetch only active courses and published classwork/announcements for the signed-in user."""
    payload = client.get(f"{CLASSROOM_API}/courses", params={"courseStates": "ACTIVE", "pageSize": 100})
    courses = [item for item in payload.get("courses", []) if isinstance(item, dict) and item.get("id")]
    result: list[dict] = []
    for course in courses[:MAX_COURSES]:
        course_id = quote(str(course["id"]), safe="")
        course_name = str(course.get("name") or "Google Classroom course")[:200]
        course_work = _pages(
            client,
            f"{CLASSROOM_API}/courses/{course_id}/courseWork",
            params={"orderBy": "updateTime desc", "courseWorkStates": "PUBLISHED"},
            rows_key="courseWork",
        )
        for item in course_work:
            item_id = str(item.get("id") or "")
            if not item_id:
                continue
            due = item.get("dueDate") or {}
            due_date = None
            if all(key in due for key in ("year", "month", "day")):
                try:
                    due_date = date(int(due["year"]), int(due["month"]), int(due["day"])).isoformat()
                except (TypeError, ValueError):
                    due_date = None
            updated = item.get("updateTime") or item.get("creationTime") or ""
            alternate_link = str(item.get("alternateLink") or "")
            if urlparse(alternate_link).hostname != "classroom.google.com":
                alternate_link = f"https://classroom.google.com/c/{course_id}/a/{item_id}/details"
            result.append({
                "external_id": f"coursework:{course_id}:{item_id}",
                "source_version": str(updated or item_id)[:256],
                "record_type": "classroom_coursework",
                "title": str(item.get("title") or "Class assignment")[:512],
                "body": str(item.get("description") or "")[:100_000],
                "url": alternate_link,
                "published_at": _parse_time(updated),
                "due_date": due_date,
                "course": course_name,
                "kind": str(item.get("workType") or "ASSIGNMENT")[:40],
                "author": "Course instructor",
            })

        materials = _pages(
            client,
            f"{CLASSROOM_API}/courses/{course_id}/courseWorkMaterials",
            params={"orderBy": "updateTime desc", "courseWorkMaterialStates": "PUBLISHED"},
            rows_key="courseWorkMaterial",
        )
        for item in materials:
            item_id = str(item.get("id") or "")
            if not item_id:
                continue
            updated = item.get("updateTime") or item.get("creationTime") or ""
            alternate_link = str(item.get("alternateLink") or "")
            if urlparse(alternate_link).hostname != "classroom.google.com":
                alternate_link = f"https://classroom.google.com/c/{course_id}"
            attached = []
            for material in item.get("materials", []):
                if not isinstance(material, dict):
                    continue
                resource = material.get("link") or material.get("driveFile") or material.get("youtubeVideo") or material.get("form") or {}
                label = str(resource.get("title") or "Class resource").strip()
                resource_url = str(resource.get("url") or resource.get("alternateLink") or "").strip()
                if label or resource_url:
                    attached.append((f"{label}: {resource_url}" if resource_url else label)[:1000])
            description = str(item.get("description") or "")
            if attached:
                description = "\n".join(part for part in (description, "Materials: " + "; ".join(attached)) if part)
            result.append({
                "external_id": f"material:{course_id}:{item_id}",
                "source_version": str(updated or item_id)[:256],
                "record_type": "classroom_material",
                "title": str(item.get("title") or "Class study material")[:512],
                "body": description[:100_000],
                "url": alternate_link,
                "published_at": _parse_time(updated),
                "course": course_name,
                "kind": "MATERIAL",
                "author": "Course instructor",
            })

        announcements = _pages(
            client,
            f"{CLASSROOM_API}/courses/{course_id}/announcements",
            params={"orderBy": "updateTime desc", "announcementStates": "PUBLISHED"},
            rows_key="announcements",
        )
        for item in announcements:
            item_id = str(item.get("id") or "")
            if not item_id:
                continue
            updated = item.get("updateTime") or item.get("creationTime") or ""
            result.append({
                "external_id": f"announcement:{course_id}:{item_id}",
                "source_version": str(updated or item_id)[:256],
                "record_type": "classroom_announcement",
                "title": f"Announcement · {course_name}",
                "body": str(item.get("text") or "")[:100_000],
                "url": f"https://classroom.google.com/c/{course_id}/a/{item_id}/details",
                "published_at": _parse_time(updated),
                "course": course_name,
                "kind": "ANNOUNCEMENT",
                "author": "Classroom member",
            })
    return result


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

