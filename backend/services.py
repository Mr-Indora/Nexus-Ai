"""Sync pipeline: Gmail -> AI extraction -> tasks; Calendar -> events; all stored in SQLite."""
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

import demo
from ai.categories import classify_category
from ai.extractor import Extractor
from integrations import calendar as gcal
from integrations import classroom as gclassroom
from integrations import college as college_feed
from integrations import gmail as ggmail
from integrations.google_api import GoogleAPIError, GoogleClient
from auth.google import GoogleAuthError
from config import settings
from models.models import (
    ActionSuggestion, Email, EmailIntelligence, Event, Task, ThreadIntelligence, User, utcnow,
)

logger = logging.getLogger(__name__)


def _google_sync_error_code(error: GoogleAPIError | httpx.HTTPError) -> str:
    """Map provider failures to stable safe identifiers, never raw provider text."""
    if isinstance(error, httpx.HTTPError):
        return "network_unavailable"
    if error.status == 401 or error.reason in {"authError", "invalidCredentials"}:
        return "authorization_expired"
    if error.status == 403 and error.reason in {"insufficientPermissions", "insufficient_authentication_scopes"}:
        return "permission_required"
    if error.status == 403 and error.reason in {"accessNotConfigured", "SERVICE_DISABLED"}:
        return "api_not_enabled"
    if error.status == 403:
        return "access_denied"
    if error.status == 429 or error.reason in {"rateLimitExceeded", "userRateLimitExceeded"}:
        return "provider_rate_limited"
    if error.status >= 500:
        return "provider_unavailable"
    return f"google_http_{error.status}"


def sync_user(db: Session, user: User) -> dict:
    warnings: list[str] = []
    fetched_emails: list[dict] = []
    fetched_events: list[dict] = []
    cancelled_google_ids: list[str] = []
    gmail_deleted_ids: list[str] = []
    gmail_cursor: str | None = None
    college_items: list[dict] = []
    classroom_items: list[dict] = []
    college_ok = classroom_ok = True
    classroom_error_code: str | None = None
    gmail_error_code: str | None = None
    calendar_error_code: str | None = None
    gmail_ok = calendar_ok = True

    # The public college feed is independent of Google login, so keep monitoring
    # it even when a user's OAuth session needs to be refreshed.
    if settings.PIDS_ENABLED:
        try:
            known_versions: dict[str, str] = {}
            try:
                from pids.persistence.models import RawRecordRow, SourceRow
                from pids.runtime import pids_session
                with pids_session() as pids_db:
                    college_source = pids_db.scalar(select(SourceRow).where(
                        SourceRow.owner_id == user.id, SourceRow.kind == "college_notices",
                    ))
                    if college_source:
                        known_versions = {
                            external_id: version
                            for external_id, version in pids_db.execute(select(
                                RawRecordRow.external_id, RawRecordRow.source_version,
                            ).where(
                                RawRecordRow.owner_id == user.id,
                                RawRecordRow.source_id == college_source.id,
                            )).all()
                        }
            except Exception:
                known_versions = {}
            college_items = college_feed.fetch_circulars(limit=25, known_versions=known_versions)
        except college_feed.CollegeFeedError:
            college_ok = False
            warnings.append("College notices: public circular feed is temporarily unavailable.")

    if user.is_demo:
        fetched_emails, fetched_events = demo.emails(), demo.events()
    else:
        client = None
        try:
            client = GoogleClient(user, db)
        except GoogleAuthError:
            # Google's refresh grant is separate from Nexus's browser session.
            # A failure here must not sign the user out of Nexus or discard the
            # independently fetched public college circulars.
            gmail_ok = calendar_ok = classroom_ok = False
            gmail_error_code = calendar_error_code = "authorization_refresh_failed"
            classroom_error_code = "classroom_permission_required"
            warnings.append(
                "Google access could not be refreshed. Reconnect Google from Connections; "
                "college notice checks can still complete."
            )
        if client:
            try:
                try:
                    if settings.PIDS_ENABLED:
                        start_history_id = None
                        try:
                            from pids.persistence.models import SourceRow, SourceSyncStateRow
                            from pids.runtime import pids_session
                            with pids_session() as pids_db:
                                source_row = pids_db.scalar(select(SourceRow).where(
                                    SourceRow.owner_id == user.id, SourceRow.kind == "gmail",
                                ))
                                if source_row:
                                    state = pids_db.get(SourceSyncStateRow, (user.id, source_row.id))
                                    start_history_id = state.cursor if state else None
                        except Exception:
                            # PIDS can be initialized after the existing Gmail sync; begin with a full window.
                            start_history_id = None
                        gmail_changes = ggmail.fetch_mailbox_updates(
                            client, start_history_id,
                            # Resume large mailboxes over multiple syncs instead of
                            # letting hundreds of Google API calls exceed the UI timeout.
                            max_results=min(settings.GMAIL_INITIAL_SYNC_MAX_EMAILS, 100),
                            days=settings.GMAIL_INITIAL_SYNC_DAYS,
                        )
                        fetched_emails = gmail_changes["messages"]
                        gmail_deleted_ids = gmail_changes["deleted_ids"]
                        gmail_cursor = gmail_changes["cursor"]
                        if gmail_changes.get("pending_history"):
                            warnings.append("More Gmail history remains; the saved cursor will continue it on the next sync.")
                    else:
                        fetched_emails = ggmail.fetch_recent_emails(
                            client,
                            max_results=min(settings.GMAIL_INITIAL_SYNC_MAX_EMAILS, 100),
                            days=settings.GMAIL_INITIAL_SYNC_DAYS,
                        )
                except (GoogleAPIError, httpx.HTTPError) as e:
                    gmail_ok = False
                    gmail_error_code = _google_sync_error_code(e)
                    if isinstance(e, GoogleAPIError) and e.status == 401:
                        warnings.append("Gmail: Google access expired or was revoked. Reconnect Google from Connections.")
                    elif isinstance(e, GoogleAPIError):
                        if e.status == 403 and e.reason in {"insufficientPermissions", "insufficient_authentication_scopes"}:
                            detail = "Gmail access was not granted. Reconnect Google and approve read-only Gmail access."
                        elif e.status == 403 and e.reason in {"accessNotConfigured", "SERVICE_DISABLED"}:
                            detail = "Gmail API is disabled for this Google Cloud project; enable Gmail API, then retry."
                        elif e.status == 403:
                            detail = "Google denied Gmail access (403). Reconnect Google with Gmail read-only permission; if it persists, check that Gmail API is enabled in Google Cloud."
                        else:
                            detail = f"Google Gmail API returned HTTP {e.status}; retry sync."
                        warnings.append(f"Gmail: {detail}")
                    else:
                        warnings.append("Gmail: network request failed; retry later")
                try:
                    fetched_events, cancelled_google_ids = gcal.fetch_event_changes(client)
                except (GoogleAPIError, httpx.HTTPError) as e:
                    calendar_ok = False
                    calendar_error_code = _google_sync_error_code(e)
                    warnings.append(
                        "Calendar: Google access expired or was revoked. Reconnect Google from Connections."
                        if isinstance(e, GoogleAPIError) and e.status == 401 else
                        f"Calendar: Google API HTTP {e.status}" if isinstance(e, GoogleAPIError)
                        else "Calendar: network request failed; retry later"
                    )
                if settings.PIDS_ENABLED:
                    try:
                        classroom_items = gclassroom.fetch_classroom_items(client)
                    except GoogleAPIError as e:
                        classroom_ok = False
                        if e.status == 401 or e.reason in {"authError", "insufficientPermissions", "PERMISSION_DENIED"}:
                            classroom_error_code = "classroom_permission_required"
                            warnings.append("Google Classroom is not authorized yet. Reconnect Google and allow the read-only Classroom access.")
                        elif e.reason in {"accessNotConfigured", "SERVICE_DISABLED"}:
                            classroom_error_code = "classroom_api_not_enabled"
                            warnings.append("Google Classroom API is not enabled for this Google Cloud project.")
                        elif e.status == 403:
                            classroom_error_code = "classroom_access_denied"
                            warnings.append("Google Classroom denied access. Check the Google Classroom permission and API setup.")
                        else:
                            classroom_error_code = "classroom_source_unavailable"
                            warnings.append(f"Google Classroom: API HTTP {e.status}.")
                    except httpx.HTTPError:
                        classroom_ok = False
                        classroom_error_code = "classroom_source_unavailable"
                        warnings.append("Google Classroom: network request failed; retry later.")
            finally:
                client.close()

    # ---- emails -> tasks
    # A historical catch-up can contain hundreds of messages. Avoid issuing one
    # paid Gemini request per message in a single sync; deterministic extraction
    # indexes the whole batch, and bounded AI features use Gemini only on relevant
    # records when consent allows it. Small routine syncs may use Gemini directly.
    cloud_extract_allowed = bool(
        user.external_ai_consent
        and settings.GEMINI_API_KEY
        and settings.LOCAL_LLM_MODE != "local-only"
        and len(fetched_emails) <= 20
    )
    if user.external_ai_consent and settings.GEMINI_API_KEY and len(fetched_emails) > 20:
        warnings.append("A large inbox was imported. Some suggestions may need another review.")
    extractor = Extractor(allow_cloud=cloud_extract_allowed)
    existing = {e.gmail_id: e for e in db.scalars(select(Email).where(Email.user_id == user.id))}
    new_tasks = 0
    new_suggestions = 0
    for raw in fetched_emails:
        row = existing.get(raw["gmail_id"])
        if row:
            content_changed = any((
                row.sender != raw.get("sender", row.sender),
                row.subject != raw.get("subject", row.subject),
                row.snippet != raw.get("snippet", row.snippet),
                row.body != raw.get("body", row.body),
                row.thread_id != raw.get("thread_id", row.thread_id),
            ))
            if content_changed:
                row.processed = False
            row.sender = raw.get("sender", row.sender)
            row.subject = raw.get("subject", row.subject)
            row.snippet = raw.get("snippet", row.snippet)
            row.body = raw.get("body", row.body)
            row.received_at = raw.get("received_at", row.received_at)
            row.thread_id = raw.get("thread_id")
        if row and row.processed:
            if not db.scalar(select(EmailIntelligence).where(
                EmailIntelligence.user_id == user.id, EmailIntelligence.email_id == row.id
            )):
                body_lower = f"{row.subject} {row.body}".lower()
                db.add(EmailIntelligence(
                    user_id=user.id, email_id=row.id, topic=row.subject[:255],
                    facts=row.snippet[:1000], response_required=any(
                        term in body_lower for term in ("please reply", "respond", "reply by", "confirm")
                    ),
                ))
            continue
        if not row:
            row = Email(user_id=user.id, gmail_id=raw["gmail_id"], thread_id=raw.get("thread_id"),
                        sender=raw["sender"], subject=raw["subject"], snippet=raw["snippet"],
                        body=raw["body"], received_at=raw["received_at"])
            db.add(row)
            db.flush()
        ex = extractor.extract(raw)
        row.processed, row.is_actionable, row.engine = True, ex.actionable, ex.engine
        if ex.actionable:
            if user.is_demo:
                db.add(Task(user_id=user.id, email_id=row.id, title=ex.title, description=ex.description,
                            category=ex.category, priority=ex.priority, deadline=ex.deadline, source="gmail"))
                new_tasks += 1
            else:
                suggestion = db.scalar(select(ActionSuggestion).where(
                    ActionSuggestion.user_id == user.id,
                    ActionSuggestion.email_id == row.id,
                    ActionSuggestion.title == ex.title,
                ))
                if not suggestion:
                    db.add(ActionSuggestion(
                        user_id=user.id, email_id=row.id, title=ex.title,
                        description=ex.description, deadline=ex.deadline,
                        priority=ex.priority, confidence="high" if ex.deadline else "medium",
                    ))
                    new_suggestions += 1
        if not db.scalar(select(EmailIntelligence).where(
            EmailIntelligence.user_id == user.id, EmailIntelligence.email_id == row.id
        )):
            body_lower = f"{row.subject} {row.body}".lower()
            db.add(EmailIntelligence(
                user_id=user.id, email_id=row.id, topic=row.subject[:255],
                facts=row.snippet[:1000], commitments=ex.description if ex.actionable else "",
                response_required=any(term in body_lower for term in ("please reply", "respond", "reply by", "confirm")),
            ))
    # Rebuild each affected thread from bounded local records; no cloud/model call.
    thread_ids = {
        row.thread_id for row in db.scalars(select(Email).where(Email.user_id == user.id))
        if row.thread_id
    }
    for thread_id in thread_ids:
        messages = db.scalars(select(Email).where(
            Email.user_id == user.id, Email.thread_id == thread_id,
        ).order_by(Email.received_at)).all()
        combined = "\n".join(f"{m.subject}\n{m.body}" for m in messages)
        lower = combined.lower()
        action_rows = [m for m in messages if m.is_actionable]
        thread = db.scalar(select(ThreadIntelligence).where(
            ThreadIntelligence.user_id == user.id, ThreadIntelligence.thread_id == thread_id,
        ))
        if not thread:
            thread = ThreadIntelligence(user_id=user.id, thread_id=thread_id)
            db.add(thread)
        thread.summary = messages[-1].subject[:255] if messages else ""
        thread.commitments = "\n".join(m.subject for m in action_rows)[:2000]
        thread.decisions = "\n".join(
            line.strip() for line in combined.splitlines()
            if any(word in line.lower() for word in ("decided", "agreed", "confirmed"))
        )[:2000]
        thread.deadlines = "\n".join(
            line.strip() for line in combined.splitlines()
            if any(word in line.lower() for word in ("deadline", "due", "by ", "before "))
        )[:2000]
        thread.pending_response = any(
            term in lower for term in ("please reply", "respond", "reply by", "awaiting your response")
        )
        thread.source_email_ids = ",".join(str(m.id) for m in messages)
        thread.processed_at = utcnow()
    if extractor.last_error:
        if "429" in extractor.last_error:
            warnings.append("AI could not review some emails right now. Some suggestions may be incomplete; try again later.")
        elif "401" in extractor.last_error or "403" in extractor.last_error:
            warnings.append("AI is unavailable right now. Please check the AI settings and try again.")
        elif "404" in extractor.last_error:
            warnings.append("AI could not review some emails. Please try again later.")
        else:
            warnings.append("AI could not review some emails right now. Please try again later.")

    # Upsert the fetched window without deleting older locally retained events.
    events_inserted = events_updated = events_unchanged = events_cancelled = 0
    if calendar_ok:
        current = {e.google_id: e for e in db.scalars(select(Event).where(Event.user_id == user.id))}
        for ev in fetched_events:
            cat = classify_category(ev["title"] + " " + ev["description"])
            row = current.get(ev["google_id"])
            if row:
                updated_values = (
                    ev["title"], ev["start"], ev["end"], ev["all_day"],
                    ev["location"], ev["meet_link"], cat,
                )
                current_values = (
                    row.title, row.start, row.end, row.all_day,
                    row.location, row.meet_link, row.category,
                )
                if current_values == updated_values:
                    events_unchanged += 1
                else:
                    (row.title, row.start, row.end, row.all_day,
                     row.location, row.meet_link, row.category) = updated_values
                    events_updated += 1
            else:
                row = Event(user_id=user.id, google_id=ev["google_id"], title=ev["title"], start=ev["start"],
                            end=ev["end"], all_day=ev["all_day"], location=ev["location"],
                            meet_link=ev["meet_link"], category=cat)
                db.add(row)
                current[ev["google_id"]] = row
                events_inserted += 1
        for google_id in cancelled_google_ids:
            row = current.get(google_id)
            if row:
                db.delete(row)
                events_cancelled += 1

    if gmail_ok or calendar_ok:
        completed_at = utcnow()
        if gmail_ok:
            user.gmail_last_synced_at = completed_at
        if calendar_ok:
            user.calendar_last_synced_at = completed_at
        if gmail_ok and calendar_ok:
            user.last_synced_at = completed_at
    db.commit()
    pids_result: dict = {"status": "disabled"}
    pids_failed = False
    if settings.PIDS_ENABLED:
        try:
            from pids.nexus_adapter import ingest_calendar_batch, ingest_gmail_batch, record_google_sync_failure
            from pids.academic_adapter import ingest_academic_items, record_academic_sync_failure
            from pids.contracts import SourceKind
            from pids.runtime import pids_session
            from pids.vault import RawDataVault

            with pids_session() as pids_db:
                vault = RawDataVault(production=not settings.DEMO_MODE)
                if gmail_ok:
                    chunks = [fetched_emails[index:index + 50] for index in range(0, len(fetched_emails), 50)] or [[]]
                    gmail_result = {"received": 0, "processed": 0, "replayed": 0,
                                    "failed": 0, "deleted": 0, "failures": []}
                    for index, chunk in enumerate(chunks):
                        result = ingest_gmail_batch(
                            pids_db, user, chunk, vault=vault,
                            source_timezone=settings.TIMEZONE,
                            retention_days=settings.PIDS_RAW_RETENTION_DAYS,
                            deleted_ids=gmail_deleted_ids if index == 0 else (),
                            history_id=(gmail_cursor if index == len(chunks) - 1 and not gmail_result["failed"] else None),
                        )
                        for key in ("received", "processed", "replayed", "failed", "deleted"):
                            gmail_result[key] += result.get(key, 0)
                        gmail_result["failures"].extend(result.get("failures", []))
                        gmail_result["sourceId"] = result.get("sourceId")
                    gmail_result["status"] = "partial" if gmail_result["failed"] else "synced"
                else:
                    gmail_result = {"status": "not_synced", "received": 0, "processed": 0, "replayed": 0, "failed": 0, "failures": []}
                calendar_result = (ingest_calendar_batch(
                    pids_db, user, fetched_events, vault=vault,
                    source_timezone=settings.TIMEZONE,
                    cancelled_ids=cancelled_google_ids,
                    retention_days=settings.PIDS_RAW_RETENTION_DAYS,
                ) if calendar_ok else {"status": "not_synced", "received": 0, "processed": 0, "failed": 0, "failures": []})
            pids_failed = bool(gmail_result["failed"] or calendar_result["failed"])
            academic_results: dict[str, dict] = {}
            if college_ok:
                try:
                    academic_results["college"] = ingest_academic_items(
                        pids_db, user, college_items,
                        source_kind=SourceKind.COLLEGE_NOTICES,
                        source_name="Engineering College Ajmer circulars",
                        identity_key="https://www.ecajmer.ac.in/allcirculars",
                        vault=vault,
                        retention_days=settings.PIDS_RAW_RETENTION_DAYS,
                    )
                except Exception as exc:
                    logger.warning("PIDS college notice processing failed for owner %s (%s)", user.id, type(exc).__name__)
                    academic_results["college"] = {"status": "failed", "processed": 0, "notifications": 0, "tasks": 0}
                    warnings.append("College notices: some items could not be analyzed; retry sync.")
                    pids_failed = True
            if classroom_ok and not user.is_demo:
                try:
                    academic_results["classroom"] = ingest_academic_items(
                        pids_db, user, classroom_items,
                        source_kind=SourceKind.GOOGLE_CLASSROOM,
                        source_name="Google Classroom",
                        identity_key=f"google-classroom:user:{user.id}",
                        vault=vault,
                        retention_days=settings.PIDS_RAW_RETENTION_DAYS,
                    )
                except Exception as exc:
                    logger.warning("PIDS Classroom processing failed for owner %s (%s)", user.id, type(exc).__name__)
                    academic_results["classroom"] = {"status": "failed", "processed": 0, "notifications": 0, "tasks": 0}
                    warnings.append("Google Classroom: some items could not be analyzed; retry sync.")
                    pids_failed = True
            for source_name, is_ok in (("college", college_ok), ("classroom", classroom_ok)):
                if not is_ok:
                    academic_results[source_name] = {"status": "unavailable", "processed": 0, "notifications": 0, "tasks": 0}
                    try:
                        with pids_session() as status_db:
                            if source_name == "college":
                                record_academic_sync_failure(
                                    status_db, user, source_kind=SourceKind.COLLEGE_NOTICES,
                                    source_name="Engineering College Ajmer circulars",
                                    identity_key="https://www.ecajmer.ac.in/allcirculars",
                                    error_code="college_feed_unavailable",
                                )
                            else:
                                record_academic_sync_failure(
                                    status_db, user, source_kind=SourceKind.GOOGLE_CLASSROOM,
                                    source_name="Google Classroom",
                                    identity_key=f"google-classroom:user:{user.id}",
                                    error_code=classroom_error_code or "classroom_source_unavailable",
                                )
                    except Exception as exc:
                        logger.warning("Could not persist academic sync status for owner %s (%s)", user.id, type(exc).__name__)
            for source_kind, is_ok, error_code in (
                (SourceKind.GMAIL, gmail_ok, gmail_error_code),
                (SourceKind.GOOGLE_CALENDAR, calendar_ok, calendar_error_code),
            ):
                if not is_ok:
                    try:
                        with pids_session() as status_db:
                            record_google_sync_failure(
                                status_db, user, source_kind=source_kind,
                                error_code=error_code or "source_unavailable",
                            )
                    except Exception as exc:
                        logger.warning("Could not persist Google sync status for owner %s (%s)", user.id, type(exc).__name__)
            pids_result = {
                "status": "partial" if pids_failed else "synced",
                "received": gmail_result.get("received", 0),
                "processed": gmail_result.get("processed", 0),
                "replayed": gmail_result.get("replayed", 0),
                "failed": gmail_result.get("failed", 0) + calendar_result.get("failed", 0),
                "gmail": gmail_result,
                "calendar": calendar_result,
                "college": academic_results.get("college", {"status": "skipped", "processed": 0, "notifications": 0, "tasks": 0}),
                "classroom": academic_results.get("classroom", {"status": "skipped", "processed": 0, "notifications": 0, "tasks": 0}),
            }
            if pids_failed:
                warnings.append(
                    "Some email or calendar updates could not be completed. Please sync again."
                )
        except Exception as exc:
            logger.warning(
                "PIDS Gmail processing failed for owner %s (%s)",
                user.id,
                type(exc).__name__,
            )
            pids_result = {"status": "failed", "errorCode": "pids_processing_failed"}
            pids_failed = True
            warnings.append("Some email insights could not be updated. Please sync again.")
    elif settings.PIDS_ENABLED:
        pids_result = {"status": "not_synced"}

    result = {
        "emailsFetched": len(fetched_emails),
        "newTasks": new_tasks,
        "newSuggestions": new_suggestions,
        "eventsSynced": len(fetched_events),
        "eventsFetched": len(fetched_events),
        "eventsInserted": events_inserted,
        "eventsUpdated": events_updated,
        "eventsUnchanged": events_unchanged,
        "eventsCancelled": events_cancelled,
        "partial": not (gmail_ok and calendar_ok and college_ok and classroom_ok) or pids_failed,
        "sources": {"gmail": "synced" if gmail_ok else "failed",
                    "calendar": "synced" if calendar_ok else "failed",
                    "college": ("synced" if college_ok else "failed") if settings.PIDS_ENABLED else "disabled",
                    "classroom": ("skipped" if user.is_demo else ("synced" if classroom_ok else "failed"))
                        if settings.PIDS_ENABLED else "disabled"},
        "engine": "gemini" if extractor.gemini_enabled or any(
            e.engine == "gemini" for e in existing.values()) else "rules",
        "warnings": warnings,
    }
    if settings.PIDS_ENABLED:
        result["sources"]["pids"] = pids_result["status"]
        result["pids"] = pids_result
    return result
