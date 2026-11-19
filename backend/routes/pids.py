"""Authenticated, bounded APIs over the isolated PIDS store."""

from datetime import datetime, timezone, date, time as local_time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
import base64
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, delete, func, inspect, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from auth.session import get_current_user
from db import get_db
from models.models import Email, Task, TaskStatusEvent, User, utcnow
from pids.identity import owner_id_from_principal
from pids.analytics import build_pids_analytics
from pids.persistence.models import (
    ActionApprovalRow,
    AuditEventRow,
    EmailMessageRow,
    EventCandidateRow,
    FeedbackRow,
    KnowledgeRecordRow,
    KnowledgeSourceRow,
    MemoryItemRow,
    RecommendationRow,
    SourceRow,
    SourceSyncStateRow,
    TaskCandidateRow,
    ThreadIntelligenceRow,
    RawRecordRow,
    NotificationPreferenceRow,
    NotificationDeliveryRow,
    Base as PidsBase,
)
from pids.runtime import pids_session
from pids.vault import RawDataVault
from config import settings
from timeutil import iso_z

router = APIRouter(prefix="/api/pids", tags=["pids"])


def get_pids_db():
    try:
        with pids_session() as session:
            yield session
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except OperationalError as exc:
        raise HTTPException(
            status_code=503,
            detail="Nexus data is temporarily busy. Wait a moment, then retry.",
        ) from exc


def _owner(user: User) -> int:
    try:
        return owner_id_from_principal(user)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid authenticated owner") from exc


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return iso_z(value)


def _recommendation_out(item: RecommendationRow) -> dict:
    evidence = item.evidence if isinstance(item.evidence, dict) else {}
    reason = item.reason or ""
    suggested_action = item.suggested_action or ""
    system_text = f"{reason} {suggested_action}".lower()
    if any(marker in system_text for marker in (
        "deterministic", "email rules", "rule-based", "rules engine", "classifier",
        "model output", "review the source message",
    )):
        sender = str(evidence.get("sender") or "").split("<", 1)[0].strip()
        subject = str(evidence.get("subject") or item.title or "this email").strip()
        reason = (
            f"{sender} sent “{subject}”. It may be worth checking against your plans."
            if sender else f"{subject} may need your attention. Check whether it applies to you."
        )
        suggested_action = "Open the email and decide whether to reply, save a task, or leave it."
    description = item.description or ""
    if any(marker in description.lower() for marker in (
        "deterministic", "email rules", "rule-based", "rules engine", "classifier", "model output",
    )):
        description = str(evidence.get("subject") or item.title or "Review this item")
    return {
        "id": item.id,
        "knowledgeId": item.knowledge_id,
        "title": item.title,
        "description": description,
        "reason": reason,
        "priority": item.priority,
        "confidence": item.confidence,
        "suggestedAction": suggested_action,
        "evidence": evidence,
        "expiresAt": _utc_iso(item.expires_at),
    }


class NotificationPreferencesInput(BaseModel):
    dailyBriefEnabled: bool = False
    dailyBriefTime: str = "06:00"
    timezone: str = "Asia/Kolkata"
    deadlineReminders: bool = True
    highPriorityAlerts: bool = True

    @field_validator("dailyBriefTime")
    @classmethod
    def validate_brief_time(cls, value: str) -> str:
        try:
            parsed = datetime.strptime(value, "%H:%M")
        except ValueError as exc:
            raise ValueError("Time must use 24-hour HH:MM format") from exc
        if parsed.strftime("%H:%M") != value:
            raise ValueError("Time must use 24-hour HH:MM format")
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Choose a valid timezone") from exc
        return value


def _notification_preferences_out(row: NotificationPreferenceRow | None) -> dict:
    return {
        "dailyBriefEnabled": row.daily_brief_enabled if row else False,
        "dailyBriefTime": row.daily_brief_time if row else "06:00",
        "timezone": row.timezone if row else "Asia/Kolkata",
        "deadlineReminders": row.deadline_reminders if row else True,
        "highPriorityAlerts": row.high_priority_alerts if row else True,
    }


def _require_notification_schema(db: Session) -> None:
    tables = set(inspect(db.get_bind()).get_table_names())
    required = {"pids_notification_preferences", "pids_notifications"}
    if not required.issubset(tables):
        raise HTTPException(
            status_code=503,
            detail="Notification storage needs the isolated PIDS update. Create an encrypted PIDS backup, then run `python -m pids.migrate` from backend.",
        )


def _new_academic_updates(
    db: Session, owner_id: int, source_kind: str, record_types: tuple[str, ...],
) -> dict:
    """Return records ingested during each source's latest successful check."""
    sources = db.scalars(select(SourceRow).where(
        SourceRow.owner_id == owner_id, SourceRow.kind == source_kind,
    )).all()
    count = 0
    items: list[dict] = []
    latest_success: datetime | None = None
    failed_latest_check = False
    for source in sources:
        state = db.get(SourceSyncStateRow, (owner_id, source.id))
        if state is None:
            continue
        if state.last_success_at is not None:
            latest_success = max(latest_success, state.last_success_at) if latest_success else state.last_success_at
        if (state.status in {"error", "partial"} or state.last_error_code
                or (state.last_attempt_at and state.last_success_at and state.last_attempt_at > state.last_success_at)):
            failed_latest_check = True
            continue
        if state.last_success_at is None or state.last_attempt_at is None:
            continue
        start, finish = state.last_attempt_at, state.last_success_at
        base_query = (
            select(KnowledgeRecordRow, RawRecordRow.ingested_at)
            .join(KnowledgeSourceRow, and_(
                KnowledgeSourceRow.owner_id == KnowledgeRecordRow.owner_id,
                KnowledgeSourceRow.knowledge_id == KnowledgeRecordRow.id,
            ))
            .join(RawRecordRow, and_(
                RawRecordRow.owner_id == KnowledgeSourceRow.owner_id,
                RawRecordRow.source_id == KnowledgeSourceRow.source_id,
                RawRecordRow.id == KnowledgeSourceRow.raw_record_id,
            ))
            .where(
                KnowledgeRecordRow.owner_id == owner_id,
                KnowledgeRecordRow.record_type.in_(record_types),
                KnowledgeSourceRow.source_id == source.id,
                RawRecordRow.ingested_at >= start,
                RawRecordRow.ingested_at <= finish,
            )
        )
        count += db.scalar(select(func.count()).select_from(base_query.subquery())) or 0
        rows = db.execute(base_query.order_by(RawRecordRow.ingested_at.desc()).limit(10)).all()
        items.extend({
            "id": record.id,
            "title": record.data.get("title", "Study update"),
            "url": record.data.get("url"),
            "sourceKind": source_kind,
            "checkedAt": _utc_iso(ingested_at),
        } for record, ingested_at in rows)
    items.sort(key=lambda item: item["checkedAt"] or "", reverse=True)
    return {
        "count": count, "items": items[:10],
        "lastSuccessfulCheckAt": _utc_iso(latest_success),
        "latestCheckFailed": failed_latest_check,
    }


def _academic_brief_line(college: dict, classroom: dict) -> str:
    parts = []
    if college["count"]:
        parts.append(f"{college['count']} new college notice(s)")
    if classroom["count"]:
        parts.append(f"{classroom['count']} new Classroom update(s)")
    items = sorted(
        college["items"] + classroom["items"],
        key=lambda item: item["checkedAt"] or "", reverse=True,
    )
    if parts:
        titles = "; ".join(item["title"] for item in items[:3])
        suffix = " One or more latest source checks failed, so other updates may be missing." if college["latestCheckFailed"] or classroom["latestCheckFailed"] else ""
        return "New since the latest successful source check: " + " and ".join(parts) + (f" — {titles}." if titles else ".") + suffix
    if college["latestCheckFailed"] or classroom["latestCheckFailed"]:
        return "The latest College/Classroom check failed; new updates are unknown until sync succeeds."
    if college["lastSuccessfulCheckAt"] or classroom["lastSuccessfulCheckAt"]:
        return "No new college or Classroom items were recorded in the latest successful checks."
    return "College and Classroom have no successful check yet."


@router.get("/notifications/preferences")
def get_notification_preferences(
    user: User = Depends(get_current_user), db: Session = Depends(get_pids_db),
):
    _require_notification_schema(db)
    return _notification_preferences_out(db.get(NotificationPreferenceRow, _owner(user)))


@router.put("/notifications/preferences")
def update_notification_preferences(
    body: NotificationPreferencesInput,
    user: User = Depends(get_current_user), db: Session = Depends(get_pids_db),
):
    _require_notification_schema(db)
    row = db.get(NotificationPreferenceRow, _owner(user))
    if row is None:
        row = NotificationPreferenceRow(owner_id=_owner(user))
        db.add(row)
    row.daily_brief_enabled = body.dailyBriefEnabled
    row.daily_brief_time = body.dailyBriefTime
    row.timezone = body.timezone
    row.deadline_reminders = body.deadlineReminders
    row.high_priority_alerts = body.highPriorityAlerts
    row.updated_at = datetime.now(timezone.utc)
    db.commit()
    return _notification_preferences_out(row)


@router.get("/notifications")
def list_notifications(
    limit: int = Query(default=30, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_pids_db),
    legacy_db: Session = Depends(get_db),
):
    _require_notification_schema(db)
    owner_id = _owner(user)
    preferences = db.get(NotificationPreferenceRow, owner_id)
    if preferences and preferences.daily_brief_enabled:
        zone = ZoneInfo(preferences.timezone)
        now = datetime.now(zone)
        scheduled = local_time.fromisoformat(preferences.daily_brief_time)
        if now.time().replace(tzinfo=None) >= scheduled:
            key = f"daily-brief:{now.date().isoformat()}"
            exists = db.scalar(select(NotificationDeliveryRow.id).where(
                NotificationDeliveryRow.owner_id == owner_id,
                NotificationDeliveryRow.delivery_key == key,
            ))
            if not exists:
                from models.models import Event, Task
                open_tasks = legacy_db.scalars(select(Task).where(
                    Task.user_id == user.id, Task.status != "completed",
                ).order_by(Task.deadline.asc().nullslast()).limit(100)).all()
                local_day = now.date()
                local_start = datetime.combine(local_day, local_time.min, tzinfo=zone).astimezone(timezone.utc).replace(tzinfo=None)
                local_end = datetime.combine(local_day, local_time.max, tzinfo=zone).astimezone(timezone.utc).replace(tzinfo=None)
                today_events = legacy_db.scalars(select(Event).where(
                    Event.user_id == user.id, Event.start >= local_start, Event.start <= local_end,
                ).order_by(Event.start).limit(100)).all()
                due_today = [
                    task for task in open_tasks
                    if task.deadline and task.deadline.replace(tzinfo=timezone.utc).astimezone(zone).date() == local_day
                ]
                high = sum(task.priority == "high" for task in open_tasks)
                first_due = next((task for task in open_tasks if task.deadline), None)
                lines = [f"You have {len(open_tasks)} open task(s), {len(due_today)} due today, and {len(today_events)} calendar event(s)."]
                if high:
                    lines.append(f"{high} task(s) are marked high priority.")
                if first_due:
                    lines.append(f"First deadline: {first_due.title}.")
                elif open_tasks:
                    lines.append(f"A good next step: {open_tasks[0].title}.")
                else:
                    lines.append("Your task list is clear; check today's calendar before planning.")
                new_college = _new_academic_updates(
                    db, owner_id, "college_notices", ("college_notice",),
                )
                new_classroom = _new_academic_updates(
                    db, owner_id, "google_classroom",
                    ("classroom_coursework", "classroom_announcement", "classroom_material"),
                )
                academic_items = sorted(
                    new_college["items"] + new_classroom["items"],
                    key=lambda item: item["checkedAt"] or "", reverse=True,
                )
                lines.append(_academic_brief_line(new_college, new_classroom))
                scheduled_label = scheduled.strftime("%I:%M %p").lstrip("0")
                brief = NotificationDeliveryRow(
                    owner_id=owner_id, delivery_key=key, kind="daily_brief",
                    title=f"Your {scheduled_label} daily brief", body=" ".join(lines),
                    data={"date": local_day.isoformat(), "taskCount": len(open_tasks),
                          "dueToday": len(due_today), "eventCount": len(today_events),
                          "collegeNoticeCount": new_college["count"],
                          "classroomUpdateCount": new_classroom["count"],
                          "academicUpdateIds": [item["id"] for item in academic_items[:10]]},
                    created_at=datetime.now(timezone.utc),
                )
                db.add(brief)
                try:
                    db.commit()
                except IntegrityError:
                    # A concurrent dashboard open may have generated today's brief.
                    db.rollback()
    rows = db.scalars(select(NotificationDeliveryRow).where(
        NotificationDeliveryRow.owner_id == owner_id,
    ).order_by(NotificationDeliveryRow.created_at.desc()).limit(limit)).all()
    return [{
        "id": row.id, "kind": row.kind, "title": row.title, "body": row.body,
        "data": row.data, "createdAt": _utc_iso(row.created_at),
        "readAt": _utc_iso(row.read_at),
    } for row in rows]


@router.post("/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: str, user: User = Depends(get_current_user),
    db: Session = Depends(get_pids_db),
):
    _require_notification_schema(db)
    row = db.scalar(select(NotificationDeliveryRow).where(
        NotificationDeliveryRow.owner_id == _owner(user),
        NotificationDeliveryRow.id == notification_id,
    ))
    if not row:
        raise HTTPException(status_code=404, detail="Notification not found")
    if row.read_at is None:
        row.read_at = datetime.now(timezone.utc)
        db.commit()
    return {"id": row.id, "readAt": _utc_iso(row.read_at)}


@router.get("/overview")
def overview(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_pids_db),
):
    owner_id = _owner(user)
    emails = db.scalars(
        select(EmailMessageRow).where(EmailMessageRow.owner_id == owner_id)
        .order_by(EmailMessageRow.received_at.desc()).limit(20)
    ).all()
    emails = [item for item in emails if "PIDS_SOURCE_DELETED" not in (item.labels or [])]
    threads = db.scalars(
        select(ThreadIntelligenceRow).where(ThreadIntelligenceRow.owner_id == owner_id)
        .order_by(ThreadIntelligenceRow.latest_message_at.desc()).limit(12)
    ).all()
    tasks = db.scalars(
        select(TaskCandidateRow).where(
            TaskCandidateRow.owner_id == owner_id,
            TaskCandidateRow.review_state.in_(("needs_review", "approved")),
            TaskCandidateRow.status != "completed",
        ).order_by(TaskCandidateRow.due_date.asc().nullslast(), TaskCandidateRow.created_at.desc())
        .limit(30)
    ).all()
    recommendations = db.scalars(
        select(RecommendationRow).where(
            RecommendationRow.owner_id == owner_id,
            RecommendationRow.status == "pending",
        ).order_by(RecommendationRow.priority, RecommendationRow.created_at.desc()).limit(20)
    ).all()
    events = db.scalars(select(EventCandidateRow).where(
        EventCandidateRow.owner_id == owner_id,
        EventCandidateRow.review_state.in_(("needs_review", "approved")),
    ).order_by(EventCandidateRow.start_at.asc().nullslast()).limit(30)).all()
    sources = db.scalars(
        select(SourceRow).where(SourceRow.owner_id == owner_id).order_by(SourceRow.created_at)
    ).all()
    source_kinds = {item.id: item.kind for item in sources}
    task_source_kinds: dict[str, str] = {}
    if tasks:
        task_source_rows = db.execute(select(
            KnowledgeSourceRow.knowledge_id, KnowledgeSourceRow.source_id,
        ).where(
            KnowledgeSourceRow.owner_id == owner_id,
            KnowledgeSourceRow.knowledge_id.in_({item.knowledge_id for item in tasks}),
        )).all()
        for knowledge_id, source_id in task_source_rows:
            if knowledge_id not in task_source_kinds and source_id in source_kinds:
                task_source_kinds[knowledge_id] = source_kinds[source_id]
    sync_states = {
        item.source_id: item for item in db.scalars(
            select(SourceSyncStateRow).where(SourceSyncStateRow.owner_id == owner_id)
        ).all()
    }
    college_source_ids = [item.id for item in sources if item.kind == "college_notices"]
    college_new_updates = _new_academic_updates(
        db, owner_id, "college_notices", ("college_notice",),
    )
    college_notices = []
    college_sync_state = next((sync_states[item.id] for item in sources if item.kind == "college_notices" and item.id in sync_states), None)
    last_college_check = college_sync_state.last_success_at if college_sync_state else None
    last_college_attempt = college_sync_state.last_attempt_at if college_sync_state else None
    new_college_notice_count = 0
    if college_source_ids:
        notice_rows = db.execute(
            select(KnowledgeRecordRow, RawRecordRow.ingested_at)
            .join(KnowledgeSourceRow, and_(
                KnowledgeSourceRow.owner_id == KnowledgeRecordRow.owner_id,
                KnowledgeSourceRow.knowledge_id == KnowledgeRecordRow.id,
            ))
            .join(RawRecordRow, and_(
                RawRecordRow.owner_id == KnowledgeSourceRow.owner_id,
                RawRecordRow.source_id == KnowledgeSourceRow.source_id,
                RawRecordRow.id == KnowledgeSourceRow.raw_record_id,
            ))
            .where(
                KnowledgeRecordRow.owner_id == owner_id,
                KnowledgeRecordRow.record_type == "college_notice",
                KnowledgeSourceRow.source_id.in_(college_source_ids),
            )
            .order_by(RawRecordRow.occurred_at.desc().nullslast(), RawRecordRow.ingested_at.desc())
            .limit(1000)
        ).all()
        if last_college_attempt and last_college_check:
            new_college_notice_count = sum(
                last_college_attempt <= ingested_at <= last_college_check
                for _, ingested_at in notice_rows
            )
        notice_ids = [item.id for item, _ in notice_rows]
        read_notice_ids = set(db.scalars(select(FeedbackRow.target_id).where(
            FeedbackRow.owner_id == owner_id,
            FeedbackRow.target_type == "college_notice",
            FeedbackRow.action == "read",
            FeedbackRow.target_id.in_(notice_ids) if notice_ids else False,
        )).all())
        college_notices = [{
            "id": item.id,
            "title": item.data.get("title", "College notice"),
            "summary": item.data.get("summary", ""),
            "topic": item.data.get("topic", ""),
            "analysisMethod": item.data.get("analysis_method", "deterministic_academic_rules_v1"),
            "url": item.data.get("url"),
            "author": item.data.get("author"),
            "publishedAt": item.data.get("published_at"),
            "checkedAt": _utc_iso(ingested_at),
            "read": item.id in read_notice_ids,
        } for item, ingested_at in notice_rows[:3]]
        college_unread_count = sum(item.id not in read_notice_ids for item, _ in notice_rows)
    else:
        college_unread_count = 0
    classroom_source_ids = [item.id for item in sources if item.kind == "google_classroom"]
    classroom_new_updates = _new_academic_updates(
        db, owner_id, "google_classroom",
        ("classroom_coursework", "classroom_announcement", "classroom_material"),
    )
    classroom_updates = []
    if classroom_source_ids:
        classroom_rows = db.execute(
            select(KnowledgeRecordRow, RawRecordRow.ingested_at)
            .join(KnowledgeSourceRow, and_(
                KnowledgeSourceRow.owner_id == KnowledgeRecordRow.owner_id,
                KnowledgeSourceRow.knowledge_id == KnowledgeRecordRow.id,
            ))
            .join(RawRecordRow, and_(
                RawRecordRow.owner_id == KnowledgeSourceRow.owner_id,
                RawRecordRow.source_id == KnowledgeSourceRow.source_id,
                RawRecordRow.id == KnowledgeSourceRow.raw_record_id,
            ))
            .where(
                KnowledgeRecordRow.owner_id == owner_id,
                KnowledgeRecordRow.record_type.in_(("classroom_coursework", "classroom_announcement", "classroom_material")),
                KnowledgeSourceRow.source_id.in_(classroom_source_ids),
            )
            .order_by(RawRecordRow.occurred_at.desc().nullslast(), RawRecordRow.ingested_at.desc())
            .limit(5)
        ).all()
        classroom_updates = [{
            "id": item.id,
            "title": item.data.get("title", "Classroom update"),
            "summary": item.data.get("summary", ""),
            "topic": item.data.get("topic", ""),
            "analysisMethod": item.data.get("analysis_method", "deterministic_academic_rules_v1"),
            "url": item.data.get("url"),
            "course": item.data.get("course"),
            "author": item.data.get("author"),
            "publishedAt": item.data.get("published_at"),
            "checkedAt": _utc_iso(ingested_at),
            "kind": item.record_type,
        } for item, ingested_at in classroom_rows]
    memories_count = db.query(MemoryItemRow).filter_by(
        owner_id=owner_id, lifecycle_status="active"
    ).count()
    analytics = build_pids_analytics(
        tasks,
        threads,
        recommendations,
        events,
        now=datetime.now(timezone.utc),
    )
    return {
        "generatedAt": _utc_iso(datetime.now(timezone.utc)),
        "analytics": analytics,
        "emails": [{
            "id": item.id,
            "externalId": item.external_id,
            "threadId": item.thread_id,
            "sender": item.sender,
            "subject": item.subject,
            "receivedAt": _utc_iso(item.received_at),
            "labels": item.labels,
            "sourceVersion": item.source_version,
        } for item in emails],
        "threads": [{
            "id": item.id,
            "threadId": item.thread_id,
            "analysis": item.analysis,
            "latestMessageAt": _utc_iso(item.latest_message_at),
            "updatedAt": _utc_iso(item.updated_at),
        } for item in threads],
        "tasks": [{
            "id": item.id,
            "knowledgeId": item.knowledge_id,
            "title": item.title,
            "description": item.description,
            "priority": item.priority,
            "status": item.status,
            "reviewState": item.review_state,
            "sourceKind": task_source_kinds.get(item.knowledge_id),
            "dueAt": _utc_iso(item.due_at),
            "dueDate": item.due_date.isoformat() if item.due_date else None,
            "createdAt": _utc_iso(item.created_at),
            "evidence": item.evidence,
        } for item in tasks],
        "recommendations": [_recommendation_out(item) for item in recommendations],
        "events": [{
            "id": item.id,
            "title": item.title,
            "startAt": _utc_iso(item.start_at),
            "endAt": _utc_iso(item.end_at),
            "eventDate": item.event_date.isoformat() if item.event_date else None,
            "allDay": item.all_day,
            "reviewState": item.review_state,
            "evidence": item.evidence,
        } for item in events if item.review_state == "needs_review"],
        "sources": [{
            "id": item.id,
            "kind": item.kind,
            "displayName": item.display_name,
            "status": item.status,
            "sync": ({
                "status": sync_states[item.id].status,
                "lastAttemptAt": _utc_iso(sync_states[item.id].last_attempt_at),
                "lastSuccessAt": _utc_iso(sync_states[item.id].last_success_at),
                "errorCode": sync_states[item.id].last_error_code,
            } if item.id in sync_states else None),
        } for item in sources],
        "collegeNotices": {
            "items": college_notices,
            "unreadCount": college_unread_count,
            "lastCheckedAt": _utc_iso(last_college_check),
            "lastAttemptAt": _utc_iso(last_college_attempt),
            "syncStatus": college_sync_state.status if college_sync_state else "not_connected",
            "errorCode": college_sync_state.last_error_code if college_sync_state else None,
            "newNoticeCount": college_new_updates["count"] if college_source_ids else new_college_notice_count,
            "newItems": college_new_updates["items"],
        },
        "classroomUpdates": {
            "items": classroom_updates,
            "newUpdateCount": classroom_new_updates["count"],
            "newItems": classroom_new_updates["items"],
            "lastCheckedAt": _utc_iso(sync_states[classroom_source_ids[0]].last_success_at)
            if classroom_source_ids and classroom_source_ids[0] in sync_states else None,
            "syncStatus": sync_states[classroom_source_ids[0]].status
            if classroom_source_ids and classroom_source_ids[0] in sync_states else "not_connected",
            "lastAttemptAt": _utc_iso(sync_states[classroom_source_ids[0]].last_attempt_at)
            if classroom_source_ids and classroom_source_ids[0] in sync_states else None,
            "errorCode": sync_states[classroom_source_ids[0]].last_error_code
            if classroom_source_ids and classroom_source_ids[0] in sync_states else None,
        },
        "activeMemoryCount": memories_count,
    }


@router.get("/college-notices")
def list_college_notices(
    limit: int = Query(default=500, ge=1, le=1000),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_pids_db),
):
    owner_id = _owner(user)
    sources = db.scalars(select(SourceRow).where(
        SourceRow.owner_id == owner_id,
        SourceRow.kind == "college_notices",
    )).all()
    source_ids = [item.id for item in sources]
    state = next(iter(db.scalars(select(SourceSyncStateRow).where(
        SourceSyncStateRow.owner_id == owner_id,
        SourceSyncStateRow.source_id.in_(source_ids) if source_ids else False,
    )).all()), None)
    if not source_ids:
        return {"items": [], "lastCheckedAt": None, "lastAttemptAt": None, "syncStatus": "not_checked"}
    rows = db.execute(
        select(KnowledgeRecordRow, RawRecordRow.ingested_at)
        .join(KnowledgeSourceRow, and_(
            KnowledgeSourceRow.owner_id == KnowledgeRecordRow.owner_id,
            KnowledgeSourceRow.knowledge_id == KnowledgeRecordRow.id,
        ))
        .join(RawRecordRow, and_(
            RawRecordRow.owner_id == KnowledgeSourceRow.owner_id,
            RawRecordRow.source_id == KnowledgeSourceRow.source_id,
            RawRecordRow.id == KnowledgeSourceRow.raw_record_id,
        ))
        .where(
            KnowledgeRecordRow.owner_id == owner_id,
            KnowledgeRecordRow.record_type == "college_notice",
            KnowledgeSourceRow.source_id.in_(source_ids),
        )
        .order_by(RawRecordRow.occurred_at.desc().nullslast(), RawRecordRow.ingested_at.desc())
        .limit(limit)
    ).all()
    notice_ids = [item.id for item, _ in rows]
    read_notice_ids = set(db.scalars(select(FeedbackRow.target_id).where(
        FeedbackRow.owner_id == owner_id,
        FeedbackRow.target_type == "college_notice",
        FeedbackRow.action == "read",
        FeedbackRow.target_id.in_(notice_ids) if notice_ids else False,
    )).all())
    return {
        "items": [{
            "id": item.id,
            "title": item.data.get("title", "College notice"),
            "summary": item.data.get("summary", ""),
            "topic": item.data.get("topic", ""),
            "url": item.data.get("url"),
            "author": item.data.get("author"),
            "publishedAt": item.data.get("published_at"),
            "checkedAt": _utc_iso(ingested_at),
            "read": item.id in read_notice_ids,
        } for item, ingested_at in rows],
        "unreadCount": sum(item.id not in read_notice_ids for item, _ in rows),
        "lastCheckedAt": _utc_iso(state.last_success_at) if state else None,
        "lastAttemptAt": _utc_iso(state.last_attempt_at) if state else None,
        "syncStatus": state.status if state else "not_checked",
    }


@router.post("/college-notices/{notice_id}/read")
def mark_college_notice_read(
    notice_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_pids_db),
):
    """Record that this owner opened a saved college notice."""
    owner_id = _owner(user)
    notice = db.scalar(select(KnowledgeRecordRow).where(
        KnowledgeRecordRow.id == notice_id,
        KnowledgeRecordRow.owner_id == owner_id,
        KnowledgeRecordRow.record_type == "college_notice",
    ))
    if notice is None:
        raise HTTPException(status_code=404, detail="College notice not found")
    already_read = db.scalar(select(FeedbackRow.id).where(
        FeedbackRow.owner_id == owner_id,
        FeedbackRow.target_type == "college_notice",
        FeedbackRow.target_id == notice_id,
        FeedbackRow.action == "read",
    ))
    if already_read:
        return {"id": notice_id, "read": True, "changed": False}
    now = datetime.now(timezone.utc)
    db.add(FeedbackRow(
        owner_id=owner_id,
        target_type="college_notice",
        target_id=notice_id,
        action="read",
        value={"readAt": now.isoformat()},
        occurred_at=now,
        created_at=now,
        updated_at=now,
    ))
    db.commit()
    return {"id": notice_id, "read": True, "changed": True}


@router.get("/emails")
def list_emails(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_pids_db),
):
    owner_id = _owner(user)
    messages = db.scalars(
        select(EmailMessageRow).where(EmailMessageRow.owner_id == owner_id)
        .order_by(EmailMessageRow.received_at.desc()).offset(offset).limit(limit)
    ).all()
    messages = [item for item in messages if "PIDS_SOURCE_DELETED" not in (item.labels or [])]
    raw_ids = [item.raw_record_id for item in messages]
    analyses = {}
    if raw_ids:
        pairs = db.execute(
            select(KnowledgeSourceRow.raw_record_id, KnowledgeRecordRow.data)
            .join(KnowledgeRecordRow, and_(
                KnowledgeRecordRow.owner_id == KnowledgeSourceRow.owner_id,
                KnowledgeRecordRow.id == KnowledgeSourceRow.knowledge_id,
            ))
            .where(
                KnowledgeSourceRow.owner_id == owner_id,
                KnowledgeSourceRow.raw_record_id.in_(raw_ids),
                KnowledgeRecordRow.record_type == "email_intelligence",
            )
        ).all()
        analyses = {raw_id: data for raw_id, data in pairs}
    return [{
        "id": item.id,
        "sender": item.sender,
        "subject": item.subject,
        "threadId": item.thread_id,
        "receivedAt": _utc_iso(item.received_at),
        "labels": item.labels,
        "sourceVersion": item.source_version,
        "analysis": analyses.get(item.raw_record_id),
    } for item in messages]


class RecommendationReview(BaseModel):
    action: Literal["approve", "reject", "dismiss"]
    reason: str | None = Field(default=None, max_length=512)


class TaskCandidateCorrection(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=2048)
    due_date: date | None = None


class TaskCandidateReview(BaseModel):
    action: Literal["approve", "reject"]
    correction: TaskCandidateCorrection | None = None


@router.post("/tasks/{task_id}/review")
def review_task_candidate(
    task_id: str,
    body: TaskCandidateReview,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_pids_db),
):
    owner_id = _owner(user)
    item = db.scalar(select(TaskCandidateRow).where(
        TaskCandidateRow.id == task_id,
        TaskCandidateRow.owner_id == owner_id,
    ))
    if item is None:
        raise HTTPException(status_code=404, detail="Task candidate not found")
    destination = "approved" if body.action == "approve" else "rejected"
    if item.review_state == destination:
        return {"id": item.id, "reviewState": item.review_state, "changed": False}
    if item.review_state != "needs_review":
        raise HTTPException(status_code=409, detail="Task candidate has already been reviewed")

    correction = body.correction
    correction_data = {}
    if correction is not None:
        changes = correction.model_dump(exclude_unset=True)
        if "title" in changes:
            item.title = changes["title"].strip()
            if not item.title:
                raise HTTPException(status_code=422, detail="Corrected title must not be empty")
            correction_data["title"] = item.title
        if "description" in changes:
            item.description = changes["description"].strip()
            correction_data["description"] = item.description
        if "due_date" in changes:
            item.due_date = changes["due_date"]
            correction_data["due_date"] = item.due_date.isoformat() if item.due_date else None

    now = datetime.now(timezone.utc)
    item.review_state = destination
    item.updated_at = now
    db.add(FeedbackRow(
        owner_id=owner_id,
        target_type="task_candidate",
        target_id=item.id,
        action="confirm" if body.action == "approve" else "reject",
        value=correction_data or None,
        occurred_at=now,
        created_at=now,
        updated_at=now,
    ))
    db.add(AuditEventRow(
        owner_id=owner_id,
        event_type=f"task_candidate_{body.action}",
        target_type="task_candidate",
        target_id=item.id,
        actor_id=owner_id,
        details={"correction": correction_data},
        created_at=now,
    ))
    db.commit()
    return {"id": item.id, "reviewState": destination, "changed": True}


@router.post("/tasks/{task_id}/complete")
def complete_task_candidate(
    task_id: str,
    user: User = Depends(get_current_user),
    pids_db: Session = Depends(get_pids_db),
    nexus_db: Session = Depends(get_db),
):
    """Let the owner close a task candidate and its linked Nexus task."""
    owner_id = _owner(user)
    item = pids_db.scalar(select(TaskCandidateRow).where(
        TaskCandidateRow.id == task_id,
        TaskCandidateRow.owner_id == owner_id,
    ))
    if item is None:
        raise HTTPException(status_code=404, detail="Task candidate not found")
    if item.status == "completed":
        return {"id": item.id, "status": "completed", "changed": False}

    linked_task_id = item.evidence.get("nexusTaskId")
    if linked_task_id is not None:
        try:
            linked_task = nexus_db.get(Task, int(linked_task_id))
        except (TypeError, ValueError):
            linked_task = None
        if linked_task and linked_task.user_id == owner_id and linked_task.status != "completed":
            previous_status = linked_task.status
            linked_task.status = "completed"
            if inspect(nexus_db.bind).has_table("task_status_events"):
                nexus_db.add(TaskStatusEvent(
                    user_id=owner_id,
                    task_id=linked_task.id,
                    from_status=previous_status,
                    to_status="completed",
                    changed_at=utcnow(),
                ))
            nexus_db.commit()

    now = datetime.now(timezone.utc)
    item.status = "completed"
    item.review_state = "approved"
    item.updated_at = now
    item.evidence = {**item.evidence, "completedByUser": True, "completedAt": now.isoformat()}
    pids_db.add(FeedbackRow(
        owner_id=owner_id,
        target_type="task_candidate",
        target_id=item.id,
        action="complete",
        value={"status": "completed"},
        occurred_at=now,
        created_at=now,
        updated_at=now,
    ))
    pids_db.add(AuditEventRow(
        owner_id=owner_id,
        event_type="task_candidate_completed",
        target_type="task_candidate",
        target_id=item.id,
        actor_id=owner_id,
        details={},
        created_at=now,
    ))
    pids_db.commit()
    return {"id": item.id, "status": "completed", "changed": True}


@router.post("/tasks/{task_id}/create-task")
def create_nexus_task_from_candidate(
    task_id: str,
    user: User = Depends(get_current_user),
    pids_db: Session = Depends(get_pids_db),
    nexus_db: Session = Depends(get_db),
):
    """Turn an explicitly approved candidate into a legacy Nexus task."""
    owner_id = _owner(user)
    candidate = pids_db.scalar(select(TaskCandidateRow).where(
        TaskCandidateRow.id == task_id, TaskCandidateRow.owner_id == owner_id,
    ))
    if candidate is None:
        raise HTTPException(status_code=404, detail="Task candidate not found")
    if candidate.review_state != "approved":
        raise HTTPException(status_code=409, detail="Approve the candidate before creating a task")
    existing_task_id = candidate.evidence.get("nexusTaskId")
    if existing_task_id:
        return {"taskId": existing_task_id, "created": False}

    raw_id = candidate.evidence.get("raw_record_id")
    external_id = None
    if raw_id:
        source_ref = pids_db.scalar(select(KnowledgeSourceRow).where(
            KnowledgeSourceRow.owner_id == owner_id,
            KnowledgeSourceRow.knowledge_id == candidate.knowledge_id,
            KnowledgeSourceRow.raw_record_id == raw_id,
        ))
        raw = pids_db.scalar(select(RawRecordRow).where(
            RawRecordRow.owner_id == owner_id,
            RawRecordRow.id == raw_id,
            RawRecordRow.source_id == (source_ref.source_id if source_ref else ""),
        )) if source_ref else None
        external_id = raw.external_id if raw else None
    source_kind = candidate.evidence.get("source_kind", "gmail")
    email = nexus_db.scalar(select(Email).where(
        Email.user_id == owner_id, Email.gmail_id == external_id,
    )) if external_id and source_kind == "gmail" else None
    task_source = source_kind if source_kind in {"gmail", "college_notices", "google_classroom"} else "gmail"
    duplicate = nexus_db.scalar(select(Task).where(
        Task.user_id == owner_id,
        Task.title == candidate.title,
        Task.description == candidate.description,
        Task.source == task_source,
        Task.email_id == (email.id if email else None),
    ).order_by(Task.id).limit(1))
    if duplicate is not None:
        candidate.evidence = {**candidate.evidence, "nexusTaskId": duplicate.id}
        candidate.updated_at = datetime.now(timezone.utc)
        pids_db.commit()
        return {"taskId": duplicate.id, "created": False}
    task = Task(
        user_id=owner_id,
        email_id=email.id if email else None,
        title=candidate.title,
        description=candidate.description,
        category="Academic" if task_source in {"college_notices", "google_classroom"} else "Informational",
        priority="high" if candidate.priority == "high" else "medium",
        deadline=(datetime.combine(candidate.due_date, datetime.min.time()) if candidate.due_date else
                  candidate.due_at.replace(tzinfo=None) if candidate.due_at else None),
        source=task_source,
        status="pending",
    )
    nexus_db.add(task)
    nexus_db.flush()
    candidate.evidence = {**candidate.evidence, "nexusTaskId": task.id}
    candidate.updated_at = datetime.now(timezone.utc)
    nexus_db.commit()
    pids_db.commit()
    return {"taskId": task.id, "created": True}


class EventCandidateReview(BaseModel):
    action: Literal["approve", "reject"]


@router.post("/events/{event_id}/review")
def review_event_candidate(
    event_id: str,
    body: EventCandidateReview,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_pids_db),
):
    owner_id = _owner(user)
    event = db.scalar(select(EventCandidateRow).where(
        EventCandidateRow.id == event_id, EventCandidateRow.owner_id == owner_id,
    ))
    if event is None:
        raise HTTPException(status_code=404, detail="Event candidate not found")
    destination = "approved" if body.action == "approve" else "rejected"
    if event.review_state == destination:
        return {"id": event.id, "reviewState": destination, "changed": False}
    if event.review_state != "needs_review":
        raise HTTPException(status_code=409, detail="Event candidate has already been reviewed")
    event.review_state = destination
    event.updated_at = datetime.now(timezone.utc)
    db.add(AuditEventRow(
        owner_id=owner_id, event_type=f"event_candidate_{body.action}",
        target_type="event_candidate", target_id=event.id, actor_id=owner_id,
        details={}, created_at=event.updated_at,
    ))
    db.commit()
    return {"id": event.id, "reviewState": destination, "changed": True}


@router.get("/data/export")
def export_pids_data(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_pids_db),
):
    """Download the authenticated owner's PIDS rows and available raw payloads."""
    owner_id = _owner(user)
    tables = {}
    for table in PidsBase.metadata.sorted_tables:
        if "owner_id" not in table.c:
            continue
        rows = db.execute(select(table).where(table.c.owner_id == owner_id)).mappings().all()
        tables[table.name] = jsonable_encoder([dict(row) for row in rows])
    raw_payloads = {}
    unavailable_payloads = []
    raw_records = db.scalars(select(RawRecordRow).where(
        RawRecordRow.owner_id == owner_id, RawRecordRow.content_ref.is_not(None),
    )).all()
    try:
        vault = RawDataVault(production=not settings.DEMO_MODE)
    except Exception:
        # Keep the data export useful when this machine cannot unlock its local
        # raw-payload vault. The export explicitly lists which payloads are
        # unavailable instead of failing the entire download with HTTP 500.
        unavailable_payloads.extend(raw.id for raw in raw_records)
        raw_records = []
    for raw in raw_records:
        try:
            payload = vault.read(owner_id, raw.content_sha256)
        except Exception:
            unavailable_payloads.append(raw.id)
            continue
        raw_payloads[raw.id] = base64.b64encode(payload).decode("ascii")
    response = JSONResponse(jsonable_encoder({
        "format": "nexus-pids-export-v1",
        "ownerId": owner_id,
        "exportedAt": datetime.now(timezone.utc),
        "tables": tables,
        "rawPayloadsBase64": raw_payloads,
        "unavailableRawPayloadIds": unavailable_payloads,
    }))
    response.headers["Content-Disposition"] = 'attachment; filename="nexus-pids-export.json"'
    return response


@router.delete("/data")
def delete_pids_data(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_pids_db),
):
    """Delete this owner's isolated PIDS records and then remove their vault blobs."""
    owner_id = _owner(user)
    raws = db.scalars(select(RawRecordRow).where(RawRecordRow.owner_id == owner_id)).all()
    digests = {row.content_sha256 for row in raws if row.content_ref}
    deleted_rows = 0
    try:
        for table in reversed(PidsBase.metadata.sorted_tables):
            if "owner_id" not in table.c or table.name == "pids_alembic_version":
                continue
            result = db.execute(delete(table).where(table.c.owner_id == owner_id))
            deleted_rows += max(result.rowcount or 0, 0)
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=409, detail="PIDS data could not be safely deleted")
    vault = RawDataVault(production=not settings.DEMO_MODE)
    removed_blobs = sum(vault.delete(owner_id, digest) for digest in digests)
    return {"deleted": True, "ownerId": owner_id, "rowsDeleted": deleted_rows,
            "vaultBlobsDeleted": removed_blobs}


@router.post("/recommendations/{recommendation_id}/review")
def review_recommendation(
    recommendation_id: str,
    body: RecommendationReview,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_pids_db),
):
    owner_id = _owner(user)
    item = db.scalar(select(RecommendationRow).where(
        RecommendationRow.id == recommendation_id,
        RecommendationRow.owner_id == owner_id,
    ))
    if item is None:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    destination = {"approve": "approved", "reject": "rejected", "dismiss": "dismissed"}[body.action]
    if item.status == destination:
        return {"id": item.id, "status": item.status, "changed": False}
    if item.status != "pending":
        raise HTTPException(status_code=409, detail="Recommendation is no longer pending")
    now = datetime.now(timezone.utc)
    item.status = destination
    db.add(FeedbackRow(
        owner_id=owner_id,
        target_type="recommendation",
        target_id=item.id,
        action=body.action,
        value={"reason": body.reason} if body.reason else None,
        occurred_at=now,
        created_at=now,
        updated_at=now,
    ))
    db.add(AuditEventRow(
        owner_id=owner_id,
        event_type=f"recommendation_{body.action}",
        target_type="recommendation",
        target_id=item.id,
        actor_id=owner_id,
        details={"reason": body.reason} if body.reason else {},
        created_at=now,
    ))
    db.commit()
    return {"id": item.id, "status": destination, "changed": True}


class ApprovalDecision(BaseModel):
    action: Literal["approve", "reject"]


@router.post("/approvals/{approval_id}/review")
def review_approval(
    approval_id: str,
    body: ApprovalDecision,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_pids_db),
):
    owner_id = _owner(user)
    item = db.scalar(select(ActionApprovalRow).where(
        ActionApprovalRow.id == approval_id,
        ActionApprovalRow.owner_id == owner_id,
    ))
    if item is None:
        raise HTTPException(status_code=404, detail="Action approval not found")
    destination = "approved" if body.action == "approve" else "rejected"
    if item.status == destination:
        return {"id": item.id, "status": item.status, "executed": False, "changed": False}
    if item.status != "pending_approval":
        raise HTTPException(status_code=409, detail="Action is no longer awaiting approval")
    now = datetime.now(timezone.utc)
    item.status = destination
    item.reviewed_at = now
    item.reviewer_id = owner_id
    db.add(AuditEventRow(
        owner_id=owner_id,
        event_type=f"action_{body.action}",
        target_type=item.action_type,
        target_id=item.target_id,
        actor_id=owner_id,
        details={"approvalId": item.id, "execution": "not_performed"},
        created_at=now,
    ))
    db.commit()
    return {"id": item.id, "status": destination, "executed": False, "changed": True}
