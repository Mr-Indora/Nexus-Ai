import json
import logging
import time as monotonic_time
from datetime import datetime, time as day_time, timedelta
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, StrictBool, field_validator, model_validator
import httpx
from sqlalchemy import and_, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ai.local_llm import LocalLLMError, local_llm
from ai.providers import AIProviderError, cloud_ai_allowed, gemini_text
from ai.extractor import Extraction, GeminiError, extract_batch_with_gemini, rule_extract
from ai.intelligence import build_intelligence_overview
from ai.recommender import recommend
from auth.google import GoogleAuthError
from auth.session import get_current_user
from config import settings
from db import check_database, get_db
from models.models import (
    ActionSuggestion, Email, EmailIntelligence, Event, MemoryCandidate, Task,
    TaskStatusEvent, ThreadIntelligence, User, UserMemory,
)
from services import sync_user
from timeutil import TZ, iso_z, local_now, local_to_utc_naive, to_local, utcnow
from integrations.calendar import URL as GOOGLE_CALENDAR_EVENTS_URL, parse_event as parse_google_event
from integrations.google_api import GoogleAPIError, GoogleClient

router = APIRouter(prefix="/api", tags=["api"])
logger = logging.getLogger(__name__)

PRI_RANK = {"high": 0, "medium": 1, "low": 2}


def task_out(t: Task) -> dict:
    email = t.email if t.email and t.email.user_id == t.user_id else None
    return {
        "id": str(t.id), "title": t.title, "description": t.description, "category": t.category,
        "priority": t.priority, "deadline": iso_z(t.deadline), "source": t.source, "status": t.status,
        "createdAt": iso_z(t.created_at),
        "emailSubject": email.subject if email else None,
        "emailSender": email.sender if email else None,
        "emailId": str(email.id) if email else None,
        "emailThreadId": email.thread_id if email else None,
        "emailReceivedAt": iso_z(email.received_at) if email else None,
    }


def event_out(e: Event) -> dict:
    return {
        "id": str(e.id), "title": e.title, "start": iso_z(e.start), "end": iso_z(e.end), "allDay": e.all_day,
        "location": e.location, "meetLink": e.meet_link, "category": e.category,
    }


def pending_sorted(db: Session, user: User) -> list[Task]:
    rows = db.scalars(select(Task).where(Task.user_id == user.id, Task.status != "completed")).all()
    far = utcnow() + timedelta(days=3650)
    return sorted(rows, key=lambda t: (PRI_RANK.get(t.priority, 1), t.deadline or far))


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/health/live")
def liveness():
    return {"ok": True}


@router.get("/health/ready")
def readiness():
    try:
        check_database()
    except Exception as exc:
        raise HTTPException(503, "Database is not ready") from exc
    return {"ok": True, "database": "ok"}


@router.get("/config")
def config():
    return {"demoMode": settings.DEMO_MODE, "googleConfigured": settings.google_configured,
            "ai": "local" if settings.LOCAL_LLM_MODE in {"local-first", "local-only"}
            else "gemini" if settings.GEMINI_API_KEY else "rules"}


@router.get("/system/status")
def system_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Expose only observed capabilities; never claim an unavailable local model is loaded."""
    check_database()
    local_status = local_llm.status()
    local_mode = settings.LOCAL_LLM_MODE
    pids_state = {
        "enabled": settings.PIDS_ENABLED,
        "status": "disabled" if not settings.PIDS_ENABLED else "unavailable",
        "schemaRevision": None,
        "notificationStorageAvailable": False,
        "errorCode": None if not settings.PIDS_ENABLED else "pids_store_unavailable",
    }
    if settings.PIDS_ENABLED:
        try:
            from pids.runtime import pids_session

            with pids_session() as pids_db:
                revision = pids_db.execute(text(
                    "SELECT version_num FROM pids_alembic_version"
                )).scalar_one_or_none()
                tables = set(inspect(pids_db.get_bind()).get_table_names())
            pids_state.update({
                "status": "connected",
                "schemaRevision": revision,
                "notificationStorageAvailable": {
                    "pids_notification_preferences", "pids_notifications",
                }.issubset(tables),
                "errorCode": None,
            })
        except RuntimeError as exc:
            message = str(exc)
            pids_state["errorCode"] = (
                "pids_schema_mismatch" if "revision does not match" in message else
                "pids_not_initialized" if "not initialized" in message or "migration state is missing" in message else
                "pids_store_unavailable"
            )
        except Exception as exc:
            logger.warning("Could not read isolated PIDS status (%s)", type(exc).__name__)
    sync_by_kind: dict[str, dict] = {}
    if settings.PIDS_ENABLED and pids_state["status"] == "connected":
        try:
            from pids.persistence.models import SourceRow, SourceSyncStateRow
            from pids.runtime import pids_session

            with pids_session() as pids_db:
                rows = pids_db.execute(
                    select(SourceRow.kind, SourceSyncStateRow.status,
                           SourceSyncStateRow.last_attempt_at, SourceSyncStateRow.last_success_at,
                           SourceSyncStateRow.last_error_code)
                    .outerjoin(SourceSyncStateRow, and_(
                        SourceSyncStateRow.owner_id == SourceRow.owner_id,
                        SourceSyncStateRow.source_id == SourceRow.id,
                    ))
                    .where(SourceRow.owner_id == user.id)
                ).all()
            for kind, sync_status, attempted, succeeded, error_code in rows:
                sync_by_kind[kind] = {
                    "status": sync_status or "not_checked",
                    "lastAttemptAt": iso_z(attempted),
                    "lastSuccessAt": iso_z(succeeded),
                    "errorCode": error_code,
                }
        except Exception as exc:
            logger.warning("Could not read per-source status for owner %s (%s)", user.id, type(exc).__name__)

    def integration_status(kind: str, *, configured: bool, legacy_success: datetime | None = None) -> dict:
        state = sync_by_kind.get(kind)
        if state:
            return {
                "configured": configured,
                "googleSessionAvailable": bool(user.access_token_enc or user.refresh_token_enc),
                "syncStatus": state["status"],
                "lastAttemptAt": state["lastAttemptAt"],
                "lastSuccessAt": state["lastSuccessAt"] or iso_z(legacy_success),
                "errorCode": state["errorCode"],
            }
        return {
            "configured": configured,
            "googleSessionAvailable": bool(user.access_token_enc or user.refresh_token_enc),
            # Legacy Gmail/Calendar tables can prove an earlier successful
            # sync even when PIDS has no per-source row yet.
            "syncStatus": "synced" if legacy_success else ("not_checked" if configured else "disabled"),
            "lastAttemptAt": None,
            "lastSuccessAt": iso_z(legacy_success),
            "errorCode": None,
        }
    return {
        "database": "connected",
        "inference": local_status | {
            "fallbackProvider": (
                "none" if local_mode == "local-only" else
                "gemini" if settings.GEMINI_API_KEY else "rules"
            ),
        },
        "externalAi": {
            "provider": "gemini",
            "configured": bool(settings.GEMINI_API_KEY),
            "consented": user.external_ai_consent,
            "enabled": bool(
                settings.GEMINI_API_KEY
                and user.external_ai_consent
                and settings.LOCAL_LLM_MODE != "local-only"
            ),
        },
        "integrations": {
            "gmail": integration_status("gmail", configured=settings.google_configured,
                                         legacy_success=user.gmail_last_synced_at) | {"requiresInternet": True},
            "calendar": integration_status("calendar", configured=settings.google_configured,
                                            legacy_success=user.calendar_last_synced_at) | {"requiresInternet": True},
            "classroom": integration_status("google_classroom",
                                             configured=settings.google_configured and settings.PIDS_ENABLED)
                | {"requiresInternet": True},
            "collegeNotices": integration_status("college_notices", configured=settings.PIDS_ENABLED)
                | {"requiresInternet": True, "googleSessionAvailable": False},
        },
        "pids": pids_state,
        "voice": {
            "stt": "browser-capability",
            "tts": "browser-capability",
            "offlineStt": False,
            "offlineTts": False,
        },
        "memoryCount": db.scalar(
            select(func.count()).select_from(UserMemory).where(UserMemory.user_id == user.id)
        ) or 0,
    }


@router.get("/memory")
def memories(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(UserMemory).where(
            UserMemory.user_id == user.id, UserMemory.active.is_(True),
            UserMemory.approval_status == "approved",
        ).order_by(UserMemory.updated_at.desc())
    ).all()
    return [{
        "id": str(row.id), "kind": row.kind, "content": row.content,
        "category": row.category, "source": row.source, "confidence": row.confidence,
        "approvalStatus": row.approval_status, "active": row.active,
        "createdAt": iso_z(row.created_at),
        "updatedAt": iso_z(row.updated_at),
    } for row in rows]


class MemoryCreate(BaseModel):
    kind: Literal["preference", "fact", "workflow"] = "preference"
    content: str

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 500:
            raise ValueError("Memory must be between 1 and 500 characters")
        return value


@router.post("/memory")
def create_memory(
    body: MemoryCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = db.scalar(select(UserMemory).where(
        UserMemory.user_id == user.id, UserMemory.content == body.content
    ))
    if existing:
        return {"id": str(existing.id), "created": False}
    memory = UserMemory(user_id=user.id, kind=body.kind, content=body.content)
    db.add(memory)
    db.commit()
    return {"id": str(memory.id), "created": True}


@router.get("/memory/candidates")
def memory_candidates(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(MemoryCandidate).where(
        MemoryCandidate.user_id == user.id, MemoryCandidate.status == "pending",
    ).order_by(MemoryCandidate.created_at.desc())).all()
    return [{"id": str(row.id), "content": row.content, "category": row.category,
             "source": row.source, "confidence": row.confidence, "status": row.status,
             "createdAt": iso_z(row.created_at)} for row in rows]


class MemoryCandidateCreate(BaseModel):
    content: str
    category: str = "preference"
    source: str = "approved-conversation"

    @field_validator("content")
    @classmethod
    def validate_candidate_content(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 500:
            raise ValueError("Candidate must be between 1 and 500 characters")
        return value


@router.post("/memory/candidates")
def create_memory_candidate(body: MemoryCandidateCreate, user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)):
    existing = db.scalar(select(MemoryCandidate).where(
        MemoryCandidate.user_id == user.id,
        MemoryCandidate.content == body.content,
        MemoryCandidate.status == "pending",
    ))
    if existing:
        return {"id": str(existing.id), "created": False}
    candidate = MemoryCandidate(user_id=user.id, content=body.content,
                                category=body.category[:32], source=body.source[:64],
                                confidence="explicit" if body.source == "approved-conversation" else "inferred")
    db.add(candidate)
    db.commit()
    return {"id": str(candidate.id), "created": True}


@router.post("/memory/candidates/{candidate_id}/approve")
def approve_memory_candidate(candidate_id: int, user: User = Depends(get_current_user),
                             db: Session = Depends(get_db)):
    candidate = db.scalar(select(MemoryCandidate).where(
        MemoryCandidate.id == candidate_id, MemoryCandidate.user_id == user.id,
    ))
    if not candidate:
        raise HTTPException(404, "Memory candidate not found")
    if candidate.status != "pending":
        raise HTTPException(409, "Memory candidate already reviewed")
    existing = db.scalar(select(UserMemory).where(
        UserMemory.user_id == user.id, UserMemory.content == candidate.content,
    ))
    if existing:
        candidate.status = "approved"
        db.commit()
        return {"id": str(existing.id), "created": False}
    memory = UserMemory(user_id=user.id, kind=candidate.category, category=candidate.category,
                        content=candidate.content, source=candidate.source,
                        confidence="user-approved", approval_status="approved")
    candidate.status = "approved"
    db.add(memory)
    db.commit()
    return {"id": str(memory.id), "created": True}


@router.post("/memory/candidates/{candidate_id}/reject")
def reject_memory_candidate(candidate_id: int, user: User = Depends(get_current_user),
                            db: Session = Depends(get_db)):
    candidate = db.scalar(select(MemoryCandidate).where(
        MemoryCandidate.id == candidate_id, MemoryCandidate.user_id == user.id,
    ))
    if not candidate:
        raise HTTPException(404, "Memory candidate not found")
    if candidate.status != "pending":
        raise HTTPException(409, "Memory candidate already reviewed")
    candidate.status = "rejected"
    db.commit()
    return {"ok": True}


class MemoryPatch(BaseModel):
    content: str | None = None
    category: str | None = None
    active: bool | None = None


@router.patch("/memory/{memory_id}")
def update_memory(memory_id: int, body: MemoryPatch, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    memory = db.scalar(select(UserMemory).where(UserMemory.id == memory_id, UserMemory.user_id == user.id))
    if not memory:
        raise HTTPException(404, "Memory not found")
    if body.content is not None:
        value = body.content.strip()
        if not value or len(value) > 500:
            raise HTTPException(422, "Memory must be between 1 and 500 characters")
        duplicate = db.scalar(select(UserMemory).where(
            UserMemory.user_id == user.id, UserMemory.content == value, UserMemory.id != memory.id,
        ))
        if duplicate:
            raise HTTPException(409, "A matching memory already exists")
        memory.content = value
    if body.category is not None:
        memory.category = body.category[:32]
        memory.kind = memory.category
    if body.active is not None:
        memory.active = body.active
    db.commit()
    return {"ok": True}


@router.delete("/memory/{memory_id}")
def delete_memory(memory_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    memory = db.get(UserMemory, memory_id)
    if not memory or memory.user_id != user.id:
        raise HTTPException(404, "Memory not found")
    db.delete(memory)
    db.commit()
    return {"ok": True}


@router.get("/email-intelligence")
def email_intelligence(
    response_required: bool | None = Query(default=None, alias="responseRequired"),
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    statement = (
        select(EmailIntelligence)
        .join(Email, Email.id == EmailIntelligence.email_id)
        .where(EmailIntelligence.user_id == user.id, Email.user_id == user.id)
    )
    if response_required is not None:
        statement = statement.where(EmailIntelligence.response_required.is_(response_required))
    rows = db.scalars(statement.order_by(EmailIntelligence.processed_at.desc()).limit(100)).all()
    return [{
        "id": str(row.id), "emailId": str(row.email_id), "subject": row.email.subject,
        "sender": row.email.sender, "topic": row.topic, "facts": row.facts,
        "decisions": row.decisions, "commitments": row.commitments,
        "responseRequired": row.response_required, "status": row.status,
        "snippet": row.email.snippet, "receivedAt": iso_z(row.email.received_at),
        "actionable": row.email.is_actionable,
        "processedAt": iso_z(row.processed_at),
    } for row in rows]


@router.get("/intelligence/overview")
def intelligence_overview(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return build_intelligence_overview(db, user)


@router.get("/action-suggestions")
def action_suggestions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(ActionSuggestion)
        .join(Email, Email.id == ActionSuggestion.email_id)
        .where(
            ActionSuggestion.user_id == user.id,
            Email.user_id == user.id,
            ActionSuggestion.status == "pending",
        )
        .order_by(ActionSuggestion.deadline.asc().nulls_last())
    ).all()
    return [{
        "id": str(row.id), "title": row.title, "description": row.description,
        "deadline": iso_z(row.deadline), "priority": row.priority, "confidence": row.confidence,
        "emailId": str(row.email_id), "subject": row.email.subject, "sender": row.email.sender,
        "createdAt": iso_z(row.created_at),
        "gmailUrl": (
            f"https://mail.google.com/mail/u/0/#all/{quote(row.email.thread_id, safe='')}"
            if row.email.thread_id else
            f"https://mail.google.com/mail/u/0/#search/{quote(f'subject:{row.email.subject}', safe='')}"
        ),
        "status": row.status,
    } for row in rows]


@router.post("/emails/reanalyze")
def reanalyze_stored_emails(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=10000),
    snapshot_max_id: int | None = Query(default=None, ge=1, alias="snapshotMaxId"),
    use_gemini: bool = Query(default=True, alias="useGemini"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Re-run bounded extraction on one page of this user's saved inbox."""
    if snapshot_max_id is None:
        snapshot_max_id = db.scalar(select(func.max(Email.id)).where(Email.user_id == user.id))
    page_filter = [Email.user_id == user.id]
    if snapshot_max_id is not None:
        page_filter.append(Email.id <= snapshot_max_id)
    total = db.scalar(select(func.count()).select_from(Email).where(*page_filter)) or 0
    rows = db.scalars(
        select(Email).where(*page_filter)
        .order_by(Email.received_at.desc(), Email.id.desc()).offset(offset).limit(limit)
    ).all()
    gemini_allowed = bool(
        use_gemini
        and user.external_ai_consent
        and settings.GEMINI_API_KEY
        and settings.LOCAL_LLM_MODE != "local-only"
    )
    gemini_rows = rows[:20] if gemini_allowed else []
    gemini_results: dict[str, Extraction] = {}
    gemini_failed = False
    if gemini_rows:
        try:
            gemini_results = extract_batch_with_gemini([{
                "id": row.id,
                "sender": row.sender,
                "subject": row.subject,
                "body": row.body,
                "snippet": row.snippet,
                "received_at": row.received_at,
            } for row in gemini_rows])
        except (GeminiError, httpx.HTTPError) as exc:
            gemini_failed = True
            logger.warning("Gemini re-analysis fell back to rules for user %s (%s)", user.id, type(exc).__name__)
    created = updated = withdrawn = 0
    gemini_processed = 0
    for email in rows:
        extracted = gemini_results.get(str(email.id))
        if extracted is not None:
            gemini_processed += 1
        else:
            extracted = rule_extract(
                email.subject,
                email.body or email.snippet,
                email.sender,
                email.received_at,
            )
        email.processed = True
        email.is_actionable = extracted.actionable
        email.engine = extracted.engine
        suggestions = db.scalars(select(ActionSuggestion).where(
            ActionSuggestion.user_id == user.id,
            ActionSuggestion.email_id == email.id,
        )).all()
        pending = [item for item in suggestions if item.status == "pending"]
        match = next((item for item in suggestions if item.title == extracted.title), None)
        if extracted.actionable:
            for stale in pending:
                if stale is not match:
                    stale.status = "withdrawn"
                    withdrawn += 1
            if match is not None and match.status == "pending":
                match.description = extracted.description
                match.deadline = extracted.deadline
                match.priority = extracted.priority
                match.confidence = "high" if extracted.deadline else "medium"
                updated += 1
            elif match is None:
                db.add(ActionSuggestion(
                    user_id=user.id, email_id=email.id, title=extracted.title,
                    description=extracted.description, deadline=extracted.deadline,
                    priority=extracted.priority,
                    confidence="high" if extracted.deadline else "medium",
                ))
                created += 1
        else:
            for stale in pending:
                stale.status = "withdrawn"
                withdrawn += 1
    db.commit()
    gemini_status = (
        "used" if gemini_processed else
        "provider_unavailable" if gemini_failed else
        "key_missing" if not settings.GEMINI_API_KEY else
        "consent_required" if not user.external_ai_consent else
        "no_emails"
    )
    return {
        "processed": len(rows), "suggestionsCreated": created,
        "suggestionsUpdated": updated, "staleSuggestionsWithdrawn": withdrawn,
        "geminiProcessed": gemini_processed,
        "rulesProcessed": len(rows) - gemini_processed,
        "geminiStatus": gemini_status if use_gemini else "rules_only",
        "engine": "mixed" if gemini_processed and gemini_processed < len(rows) else "gemini" if gemini_processed else "rules",
        "total": total,
        "offset": offset,
        "nextOffset": offset + len(rows) if offset + len(rows) < total else None,
        "hasMore": offset + len(rows) < total,
        "snapshotMaxId": snapshot_max_id,
    }


@router.post("/action-suggestions/{suggestion_id}/approve")
def approve_action_suggestion(suggestion_id: int, user: User = Depends(get_current_user),
                              db: Session = Depends(get_db)):
    suggestion = db.scalar(select(ActionSuggestion).where(
        ActionSuggestion.id == suggestion_id,
        ActionSuggestion.user_id == user.id,
    ).join(Email, Email.id == ActionSuggestion.email_id).where(Email.user_id == user.id))
    if not suggestion:
        raise HTTPException(404, "Action suggestion not found")
    if suggestion.status != "pending":
        raise HTTPException(409, "Action suggestion already reviewed")
    existing = db.scalar(select(Task).where(
        Task.user_id == user.id, Task.email_id == suggestion.email_id, Task.title == suggestion.title,
    ))
    task = existing or Task(
        user_id=user.id, email_id=suggestion.email_id, title=suggestion.title,
        description=suggestion.description, priority=suggestion.priority,
        deadline=suggestion.deadline, source="gmail",
    )
    if not existing:
        db.add(task)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            task = db.scalar(select(Task).where(
                Task.user_id == user.id, Task.email_id == suggestion.email_id,
                Task.title == suggestion.title,
            ))
            if not task:
                raise HTTPException(409, "Approval conflicted; retry the request") from None
            suggestion = db.get(ActionSuggestion, suggestion_id)
            if not suggestion:
                raise HTTPException(404, "Action suggestion not found")
    suggestion.status, suggestion.task_id, suggestion.reviewed_at = "approved", task.id, utcnow()
    db.commit()
    return {"ok": True, "taskId": str(task.id), "created": not bool(existing)}


@router.post("/action-suggestions/{suggestion_id}/reject")
def reject_action_suggestion(suggestion_id: int, user: User = Depends(get_current_user),
                             db: Session = Depends(get_db)):
    suggestion = db.scalar(select(ActionSuggestion).where(
        ActionSuggestion.id == suggestion_id, ActionSuggestion.user_id == user.id,
    ))
    if not suggestion:
        raise HTTPException(404, "Action suggestion not found")
    if suggestion.status != "pending":
        raise HTTPException(409, "Action suggestion already reviewed")
    suggestion.status, suggestion.reviewed_at = "rejected", utcnow()
    db.commit()
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "name": user.name, "email": user.email, "picture": user.picture,
            "isDemo": user.is_demo, "lastSyncedAt": iso_z(user.last_synced_at),
            "gmailLastSyncedAt": iso_z(user.gmail_last_synced_at),
            "calendarLastSyncedAt": iso_z(user.calendar_last_synced_at),
            "externalAiConsent": user.external_ai_consent}


class ExternalAIConsentUpdate(BaseModel):
    enabled: StrictBool


@router.patch("/privacy/ai-consent")
def update_external_ai_consent(
    body: ExternalAIConsentUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user.external_ai_consent = body.enabled
    db.commit()
    return {"externalAiConsent": user.external_ai_consent}


@router.post("/sync")
def sync(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        return sync_user(db, user)
    except PermissionError as e:
        raise HTTPException(401, str(e))
    except Exception as e:
        db.rollback()
        logger.error("Sync failed for user_id=%s (%s)", user.id, type(e).__name__)
        raise HTTPException(
            500, "Sync failed during local processing; the last successful sync was preserved."
        ) from e


@router.get("/tasks")
def tasks(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Task).where(Task.user_id == user.id).order_by(Task.created_at.desc())).all()
    return [task_out(t) for t in rows]


class TaskPatch(BaseModel):
    status: Literal["pending", "in-progress", "completed"] | None = None
    priority: Literal["high", "medium", "low"] | None = None


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    description: str = Field(default="", max_length=4000)
    category: Literal["Academic", "Administrative", "Career", "Informational"] = "Informational"
    priority: Literal["high", "medium", "low"] = "medium"
    deadline: datetime | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Task title must not be empty")
        return value

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str) -> str:
        return value.strip()


@router.post("/tasks", status_code=201)
def create_task(body: TaskCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = Task(
        user_id=user.id,
        title=body.title,
        description=body.description,
        category=body.category,
        priority=body.priority,
        deadline=local_to_utc_naive(body.deadline) if body.deadline else None,
        source="manual",
        status="pending",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task_out(task)


@router.post("/ai/provider-check")
def ai_provider_check(user: User = Depends(get_current_user)):
    """Check Gemini configuration with a fixed prompt that contains no user data."""
    if settings.LOCAL_LLM_MODE == "local-only":
        status = local_llm.status()
        if status["offlineReady"]:
            return {"status": "available", "message": "Local AI is ready; no data is sent to Gemini."}
        message = (
            f"Start Ollama and download {settings.OLLAMA_MODEL}, then retry."
            if settings.LOCAL_LLM_PROVIDER == "ollama" else
            "The local model runtime or model file is not ready yet. Check the local model settings and retry."
        )
        return {"status": "local_unavailable", "message": message}
    if not settings.GEMINI_API_KEY:
        return {"status": "not_configured", "message": "AI setup is not finished yet."}
    if not user.external_ai_consent:
        return {"status": "consent_required", "message": "Turn on AI processing in Settings before checking this feature."}
    if not cloud_ai_allowed(user):
        return {"status": "blocked_by_mode", "message": "AI features are unavailable with the current settings."}

    started = monotonic_time.monotonic()
    try:
        response = gemini_text(
            "Reply with the single word READY.",
            "This is a provider connection test. No personal data was included.",
            timeout=25,
        )
        if not response.strip():
            return {"status": "provider_error", "errorCode": "empty_response",
                    "message": "AI did not respond. Please try again."}
        return {"status": "available", "message": "AI is ready to help.",
                "responseTimeMs": round((monotonic_time.monotonic() - started) * 1000)}
    except (LocalLLMError, AIProviderError, httpx.HTTPError) as exc:
        error_text = str(exc)
        status_code = next((code for code in (401, 403, 404, 429, 500, 502, 503, 504)
                            if str(code) in error_text), None)
        error_code = (
            "key_or_access_error" if status_code in {401, 403} else
            "model_unavailable" if status_code == 404 else
            "quota_exceeded" if status_code == 429 else
            "provider_unavailable" if status_code or isinstance(exc, httpx.HTTPError) else
            "provider_error"
        )
        messages = {
            "key_or_access_error": "AI setup needs attention. Please try again after setup is checked.",
            "model_unavailable": "AI setup needs an update before it can be used. Please try again after setup is checked.",
            "quota_exceeded": "AI is busy right now. Please try again later.",
            "provider_unavailable": "Google AI is temporarily unavailable. Please try again in a moment.",
            "provider_error": "AI could not complete the check. Please try again.",
        }
        logger.warning("Gemini provider check failed for user %s (%s)", user.id, type(exc).__name__)
        return {"status": "provider_error", "errorCode": error_code,
                "message": messages[error_code]}


@router.get("/thread-intelligence")
def thread_intelligence(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(ThreadIntelligence).where(
        ThreadIntelligence.user_id == user.id,
    ).order_by(ThreadIntelligence.processed_at.desc()).limit(50)).all()
    return [{
        "id": str(row.id), "threadId": row.thread_id, "summary": row.summary,
        "decisions": row.decisions, "commitments": row.commitments,
        "deadlines": row.deadlines, "pendingResponse": row.pending_response,
        "sourceEmailIds": row.source_email_ids.split(",") if row.source_email_ids else [],
        "processedAt": iso_z(row.processed_at),
    } for row in rows]


@router.patch("/tasks/{task_id}")
def patch_task(task_id: int, body: TaskPatch, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    t = db.get(Task, task_id)
    if not t or t.user_id != user.id:
        raise HTTPException(404, "Task not found")
    if body.status and body.status != t.status:
        previous_status = t.status
        t.status = body.status
        if inspect(db.bind).has_table("task_status_events"):
            db.add(TaskStatusEvent(
                user_id=user.id, task_id=t.id,
                from_status=previous_status, to_status=body.status,
                changed_at=utcnow(),
            ))
    if body.priority:
        t.priority = body.priority
    db.commit()
    return task_out(t)


@router.get("/analytics/task-completions")
def task_completion_analytics(
    days: int = Query(default=7, ge=7, le=30),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not inspect(db.bind).has_table("task_status_events"):
        return {
            "available": False, "days": [], "totalCompleted": 0,
            "message": "Task completion history starts after database update 0009; previous completion dates are not inferred.",
        }
    today = local_now().date()
    first_day = today - timedelta(days=days - 1)
    start_at = local_to_utc_naive(datetime.combine(first_day, day_time.min, tzinfo=TZ))
    rows = db.scalars(select(TaskStatusEvent).where(
        TaskStatusEvent.user_id == user.id,
        TaskStatusEvent.to_status == "completed",
        TaskStatusEvent.changed_at >= start_at,
    )).all()
    counts = {first_day + timedelta(days=index): 0 for index in range(days)}
    for row in rows:
        local_day = to_local(row.changed_at).date()
        if local_day in counts:
            counts[local_day] += 1
    return {
        "available": True,
        "timezone": settings.TIMEZONE,
        "days": [{"date": day.isoformat(), "completed": count} for day, count in counts.items()],
        "totalCompleted": sum(counts.values()),
        "trackingNote": "Counts status changes to Completed recorded after database update 0009. Older completion dates are unknown.",
    }


@router.delete("/tasks/{task_id}")
def delete_task(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    t = db.get(Task, task_id)
    if not t or t.user_id != user.id:
        raise HTTPException(404, "Task not found")
    db.delete(t)
    db.commit()
    return {"ok": True}


@router.get("/emails")
def emails(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Email).where(Email.user_id == user.id).order_by(Email.received_at.desc()).limit(30)
    ).all()
    return [{"id": str(e.id), "sender": e.sender, "subject": e.subject, "snippet": e.snippet,
             "receivedAt": iso_z(e.received_at), "actionable": e.is_actionable,
             "taskId": str(e.task.id) if e.task else None, "engine": e.engine} for e in rows]


@router.get("/emails/{email_id}")
def email_detail(email_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    email = db.scalar(select(Email).where(Email.id == email_id, Email.user_id == user.id))
    if not email:
        raise HTTPException(404, "Email not found")
    timeline_rows = db.scalars(
        select(Email).where(
            Email.user_id == user.id,
            Email.thread_id == email.thread_id,
        ).order_by(Email.received_at.asc())
    ).all() if email.thread_id else [email]
    return {
        "id": str(email.id),
        "sender": email.sender,
        "subject": email.subject,
        "snippet": email.snippet,
        "body": email.body,
        "receivedAt": iso_z(email.received_at),
        "gmailUrl": (
            f"https://mail.google.com/mail/u/0/#all/{email.thread_id}"
            if email.thread_id else
            f"https://mail.google.com/mail/u/0/#search/{quote(f'subject:{email.subject}', safe='')}"
        ),
        "timelineCoverage": "synced_incoming_messages_only",
        "timeline": [{
            "id": str(item.id),
            "sender": item.sender,
            "subject": item.subject,
            "receivedAt": iso_z(item.received_at),
            "snippet": item.snippet,
        } for item in timeline_rows],
    }


@router.get("/events")
def events(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Event).where(Event.user_id == user.id).order_by(Event.start)).all()
    return [event_out(e) for e in rows]


class CalendarEventWrite(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    start: datetime
    end: datetime
    location: str = Field(default="", max_length=512)
    description: str | None = Field(default=None, max_length=8000)
    all_day: bool = False

    @field_validator("title", "location", "description")
    @classmethod
    def trim_calendar_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_range(self):
        if not self.title.strip():
            raise ValueError("Event title is required")
        start = self.start if self.start.tzinfo else self.start.replace(tzinfo=timezone.utc)
        end = self.end if self.end.tzinfo else self.end.replace(tzinfo=timezone.utc)
        if start >= end:
            raise ValueError("Event end must be after its start")
        return self


def _calendar_payload(body: CalendarEventWrite) -> dict:
    if body.all_day:
        start_value = {"date": body.start.date().isoformat()}
        end_value = {"date": body.end.date().isoformat()}
    else:
        start = body.start if body.start.tzinfo else body.start.replace(tzinfo=timezone.utc)
        end = body.end if body.end.tzinfo else body.end.replace(tzinfo=timezone.utc)
        start_value = {"dateTime": start.isoformat()}
        end_value = {"dateTime": end.isoformat()}
    payload = {
        "summary": body.title,
        "location": body.location,
        "start": start_value,
        "end": end_value,
    }
    if body.description is not None:
        payload["description"] = body.description
    return payload


def _write_calendar_event(user: User, db: Session, payload: dict, existing: Event | None = None) -> Event:
    client = None
    try:
        client = GoogleClient(user, db)
        url = GOOGLE_CALENDAR_EVENTS_URL if existing is None else f"{GOOGLE_CALENDAR_EVENTS_URL}/{quote(existing.google_id, safe='')}"
        response = client.post(url, payload) if existing is None else client.patch(url, payload)
    except GoogleAuthError as exc:
        raise HTTPException(status_code=401, detail="Reconnect Google to enable Calendar event changes.") from exc
    except GoogleAPIError as exc:
        status = exc.status
        if status == 404:
            if existing is None:
                raise HTTPException(status_code=404, detail="Google Calendar could not find the primary calendar for this account. Reconnect the Google account you want to use, then try again.") from exc
            raise HTTPException(status_code=404, detail="Google Calendar no longer has this event. Sync your calendar and try again.") from exc
        if status in {401, 403}:
            raise HTTPException(status_code=403, detail="Google denied Calendar edit access. Refresh Google permissions and allow Calendar event changes.") from exc
        raise HTTPException(status_code=502, detail="Google Calendar could not save this event. Try again.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Google Calendar could not be reached. Try again.") from exc
    finally:
        if client is not None:
            client.close()
    parsed = parse_google_event(response)
    if not parsed:
        raise HTTPException(status_code=502, detail="Google Calendar saved an event Nexus could not read back.")
    row = existing or Event(user_id=user.id, google_id=parsed["google_id"])
    row.google_id = parsed["google_id"]
    row.title = parsed["title"]
    row.start = parsed["start"]
    row.end = parsed["end"]
    row.all_day = parsed["all_day"]
    row.location = parsed["location"]
    row.meet_link = parsed["meet_link"]
    row.category = "Informational"
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.post("/calendar/events")
def create_calendar_event(
    body: CalendarEventWrite,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return event_out(_write_calendar_event(user, db, _calendar_payload(body)))


@router.patch("/calendar/events/{event_id}")
def update_calendar_event(
    event_id: str,
    body: CalendarEventWrite,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = None
    try:
        local_event_id = int(event_id)
    except (TypeError, ValueError):
        local_event_id = None
    if local_event_id is not None:
        row = db.scalar(select(Event).where(Event.id == local_event_id, Event.user_id == user.id))
    if row is None:
        row = db.scalar(select(Event).where(Event.google_id == event_id, Event.user_id == user.id))
    if row is None:
        raise HTTPException(status_code=404, detail="This event is no longer in your saved calendar. Sync Google Calendar, then try again.")
    return event_out(_write_calendar_event(user, db, _calendar_payload(body), row))


@router.get("/recommendation")
def recommendation(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    day_start = local_now().replace(hour=0, minute=0, second=0, microsecond=0)
    lo, hi = local_to_utc_naive(day_start), local_to_utc_naive(day_start + timedelta(days=1))
    today = db.scalars(select(Event).where(Event.user_id == user.id, Event.start >= lo, Event.start < hi)
                       .order_by(Event.start)).all()
    ev = [{"id": e.id, "title": e.title, "start": e.start, "all_day": e.all_day, "meet_link": e.meet_link} for e in today]
    return recommend(pending_sorted(db, user), ev, allow_cloud=cloud_ai_allowed(user))


@router.get("/sources")
def sources(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    n_mail = db.scalar(select(func.count()).select_from(Email).where(Email.user_id == user.id)) or 0
    n_ev = db.scalar(select(func.count()).select_from(Event).where(Event.user_id == user.id)) or 0
    # OAuth authorization and successful data sync are separate facts. The old
    # response marked both sources connected even when a user's sync had never
    # succeeded (or had started failing), which made empty dashboards misleading.
    sync_by_kind: dict[str, dict] = {}
    if settings.PIDS_ENABLED:
        try:
            from pids.persistence.models import SourceRow, SourceSyncStateRow
            from pids.runtime import pids_session
            with pids_session() as pids_db:
                source_rows = pids_db.scalars(select(SourceRow).where(
                    SourceRow.owner_id == user.id,
                    SourceRow.kind.in_(("gmail", "calendar")),
                )).all()
                for source_row in source_rows:
                    state = pids_db.get(SourceSyncStateRow, (user.id, source_row.id))
                    if state:
                        sync_by_kind[source_row.kind] = {
                            "status": state.status,
                            "lastAttemptAt": iso_z(state.last_attempt_at),
                            "lastSuccessAt": iso_z(state.last_success_at),
                            "errorCode": state.last_error_code,
                        }
        except Exception as exc:
            logger.warning("Could not read source sync state for owner %s (%s)", user.id, type(exc).__name__)

    def source_status(kind: str, last_synced: datetime | None) -> tuple[str, str | None]:
        if user.is_demo:
            return "active", None
        if not (user.access_token_enc or user.refresh_token_enc):
            return "disconnected", None
        state = sync_by_kind.get(kind)
        if state and state["status"] in ("error", "partial"):
            return "connected", state["errorCode"] or "sync_failed"
        return "connected", None

    gmail_status, gmail_error = source_status("gmail", user.gmail_last_synced_at)
    calendar_status, calendar_error = source_status("calendar", user.calendar_last_synced_at)
    gmail_last_synced = iso_z(user.gmail_last_synced_at) or sync_by_kind.get("gmail", {}).get("lastSuccessAt")
    calendar_last_synced = iso_z(user.calendar_last_synced_at) or sync_by_kind.get("calendar", {}).get("lastSuccessAt")
    return [
        {"id": "gmail", "name": "Gmail", "type": "gmail", "status": gmail_status,
         "description": "Reads recent inbox emails (read-only) and extracts tasks with AI.",
         "lastSynced": gmail_last_synced, "itemCount": n_mail,
         "syncStatus": sync_by_kind.get("gmail", {}).get("status", "never_synced" if not gmail_last_synced else "synced"),
         "lastSyncError": gmail_error},
        {"id": "calendar", "name": "Google Calendar", "type": "calendar", "status": calendar_status,
         "description": "Reads your events and can create or update events you choose.",
         "lastSynced": calendar_last_synced, "itemCount": n_ev,
         "syncStatus": sync_by_kind.get("calendar", {}).get("status", "never_synced" if not calendar_last_synced else "synced"),
         "lastSyncError": calendar_error},
    ]
