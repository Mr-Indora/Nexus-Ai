"""Read-only connector for Engineering College Ajmer's public circular feed."""

from __future__ import annotations

from datetime import date, datetime, timezone
from io import BytesIO
import re
from urllib.parse import urljoin, urlparse

import httpx

COLLEGE_ORIGIN = "https://ecajmer.ac.in"
CIRCULARS_API = f"{COLLEGE_ORIGIN}/api/circulars/public/all"
MAX_CIRCULARS_PER_SYNC = 100
MAX_PDF_BYTES = 8 * 1024 * 1024
MAX_PDF_PAGES = 20
MAX_EXTRACTED_TEXT = 100_000
# Keep public feed sync bounded: the notice metadata and links are retained for
# every circular, while only a few PDFs are read in any one pass.
MAX_PDF_DOWNLOADS_PER_SYNC = 3
PDF_DOWNLOAD_TIMEOUT_SECONDS = 8


class CollegeFeedError(RuntimeError):
    """Safe, non-sensitive public college feed failure."""


def _official_url(value: str) -> str:
    url = urljoin(f"{COLLEGE_ORIGIN}/", value)
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"ecajmer.ac.in", "www.ecajmer.ac.in"}:
        raise CollegeFeedError("college_feed_invalid_link")
    return url


def extract_pdf_text(client: httpx.Client, url: str) -> str:
    """Extract bounded text when pypdf is installed; otherwise leave the link usable."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""

    pdf_url = _official_url(url)
    try:
        with client.stream("GET", pdf_url, follow_redirects=True, timeout=httpx.Timeout(PDF_DOWNLOAD_TIMEOUT_SECONDS, connect=4)) as response:
            if response.status_code != 200:
                return ""
            if urlparse(str(response.url)).hostname not in {"ecajmer.ac.in", "www.ecajmer.ac.in"}:
                return ""
            chunks = bytearray()
            for chunk in response.iter_bytes():
                if len(chunks) + len(chunk) > MAX_PDF_BYTES:
                    return ""
                chunks.extend(chunk)
    except httpx.HTTPError:
        return ""
    if not chunks.startswith(b"%PDF-"):
        return ""
    try:
        reader = PdfReader(BytesIO(chunks), strict=False)
        text = "\n".join(page.extract_text() or "" for page in reader.pages[:MAX_PDF_PAGES])
    except Exception:
        return ""
    return text[:MAX_EXTRACTED_TEXT]


def fetch_circulars(*, limit: int = MAX_CIRCULARS_PER_SYNC, known_versions: dict[str, str] | None = None) -> list[dict]:
    if not 1 <= limit <= MAX_CIRCULARS_PER_SYNC:
        raise ValueError("college circular limit is outside the supported range")
    try:
        response = httpx.get(
            CIRCULARS_API,
            params={"page": 1, "limit": limit},
            timeout=25,
            follow_redirects=True,
            headers={"Accept": "application/json", "User-Agent": "NexusAI-NoticeReader/1.0"},
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise CollegeFeedError("college_feed_unavailable") from exc
    if not isinstance(payload, dict) or payload.get("success") is not True or not isinstance(payload.get("data"), list):
        raise CollegeFeedError("college_feed_invalid_response")

    items: list[dict] = []
    known_versions = known_versions or {}
    pdf_attempts = 0
    with httpx.Client(follow_redirects=True, headers={"User-Agent": "NexusAI-NoticeReader/1.0"}) as client:
        for row in payload["data"][:limit]:
            if not isinstance(row, dict) or row.get("isDeleted"):
                continue
            title = " ".join(str(row.get("title") or "College circular").split())[:512]
            external_id = str(row.get("id") or row.get("sno") or "").strip()
            if not external_id:
                continue
            content = str(row.get("content") or "")
            link = _official_url(content) if content else f"{COLLEGE_ORIGIN}/allcirculars"
            published = None
            for raw_date in (row.get("updatedAt"), row.get("createdAt"), row.get("date")):
                if not raw_date:
                    continue
                try:
                    published = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
                    if published.tzinfo is None:
                        published = published.replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    try:
                        published = datetime.combine(date.fromisoformat(str(raw_date)[:10]), datetime.min.time(), tzinfo=timezone.utc)
                        break
                    except ValueError:
                        pass
            is_pdf = str(row.get("contentType", "")).lower() == "pdf"
            already_read = known_versions.get(external_id, "").endswith(":text-v1")
            body = ""
            if is_pdf and not already_read and pdf_attempts < MAX_PDF_DOWNLOADS_PER_SYNC:
                pdf_attempts += 1
                body = extract_pdf_text(client, link)
            content_extracted = bool(body) or already_read
            category = row.get("department", {}).get("name") or row.get("cell", {}).get("name")
            base_version = str(row.get("updatedAt") or row.get("date") or external_id)
            version = f"{base_version}:{'text-v1' if content_extracted else 'metadata-v1'}"[:256]
            items.append({
                "external_id": external_id,
                "source_version": version,
                "record_type": "college_notice",
                "title": title,
                "body": body,
                "url": link,
                "published_at": published,
                "category": str(category or "College circular")[:120],
                "author": str(row.get("circularFrom") or category or "").strip()[:200] or None,
                "notice_number": str(row.get("sno") or "")[:80],
                "content_extracted": content_extracted,
            })
    return items

