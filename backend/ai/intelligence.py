"""Deterministic, user-scoped metrics for the primary intelligence dashboard."""
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai.decision import build_daily_decision
from models.models import Email, EmailIntelligence, Event, Task, User, UserMemory
from timeutil import TZ, iso_z, local_to_utc_naive, to_local, utcnow


def build_intelligence_overview(
    db: Session, user: User, now: datetime | None = None,
) -> dict:
    now = now or utcnow()
    today = to_local(now).date()
    tomorrow = today + timedelta(days=1)
    next_week = today + timedelta(days=7)

    tasks = db.scalars(select(Task).where(Task.user_id == user.id)).all()
    pending = [task for task in tasks if task.status != "completed"]
    pending_with_deadlines = [task for task in pending if task.deadline]
    by_priority = {
        priority: sum(task.priority == priority for task in pending)
        for priority in ("high", "medium", "low")
    }

    def deadline_date(task: Task) -> date | None:
        return to_local(task.deadline).date() if task.deadline else None

    task_metrics = {
        "total": len(tasks),
        "completed": len(tasks) - len(pending),
        "pending": len(pending),
        "overdue": sum(task.deadline is not None and task.deadline < now for task in pending),
        "dueToday": sum(
            deadline_date(task) == today and task.deadline >= now
            for task in pending_with_deadlines
        ),
        "dueTomorrow": sum(deadline_date(task) == tomorrow for task in pending_with_deadlines),
        "upcomingSevenDays": sum(
            task.deadline >= now and today <= deadline_date(task) < next_week
            for task in pending_with_deadlines
        ),
        "missingDeadlines": sum(task.deadline is None for task in pending),
        "longPending": sum(
            task.created_at <= now - timedelta(days=14) for task in pending
        ),
        "byPriority": by_priority,
    }

    email_count = db.scalar(
        select(func.count()).select_from(Email).where(Email.user_id == user.id)
    ) or 0
    actionable_count = db.scalar(
        select(func.count()).select_from(Email).where(
            Email.user_id == user.id, Email.is_actionable.is_(True),
        )
    ) or 0
    response_requests = db.scalar(
        select(func.count()).select_from(EmailIntelligence)
        .join(Email, Email.id == EmailIntelligence.email_id).where(
            EmailIntelligence.user_id == user.id,
            Email.user_id == user.id,
            EmailIntelligence.response_required.is_(True),
        )
    ) or 0

    window_start = local_to_utc_naive(datetime.combine(today, time.min, tzinfo=TZ)) - timedelta(days=1)
    window_end = local_to_utc_naive(datetime.combine(next_week, time.min, tzinfo=TZ)) + timedelta(days=1)
    events = db.scalars(
        select(Event).where(
            Event.user_id == user.id,
            Event.start >= window_start,
            Event.start < window_end,
        ).order_by(Event.start)
    ).all()

    def event_day(event: Event) -> date:
        return event.start.date() if event.all_day else to_local(event.start).date()

    in_window = [event for event in events if today <= event_day(event) < next_week]
    upcoming = [
        event for event in in_window
        if event.all_day or (event.end or event.start) >= now
    ]
    timed_events = [
        event for event in upcoming
        if not event.all_day and event.end is not None and event.end > event.start
    ]
    timed_events.sort(key=lambda event: event.start)
    conflicts = []
    for index, first in enumerate(timed_events):
        for second in timed_events[index + 1:]:
            if second.start >= first.end:
                break
            if second.start < first.end and second.end > first.start:
                conflicts.append({
                    "eventIds": [str(first.id), str(second.id)],
                    "titles": [first.title, second.title],
                    "start": iso_z(max(first.start, second.start)),
                    "end": iso_z(min(first.end, second.end)),
                })

    approved_memories = (
        UserMemory.user_id == user.id,
        UserMemory.active.is_(True),
        UserMemory.approval_status == "approved",
    )
    memory_rows = db.scalars(
        select(UserMemory).where(*approved_memories)
        .order_by(UserMemory.updated_at.desc()).limit(3)
    ).all()
    memory_count = db.scalar(
        select(func.count()).select_from(UserMemory).where(*approved_memories)
    ) or 0
    goal_count = db.scalar(
        select(func.count()).select_from(UserMemory).where(
            *approved_memories, UserMemory.category == "goal",
        )
    ) or 0
    project_count = db.scalar(
        select(func.count()).select_from(UserMemory).where(
            *approved_memories, UserMemory.category == "project",
        )
    ) or 0

    return {
        "generatedAt": iso_z(now),
        "dailyDecision": build_daily_decision(tasks, now),
        "tasks": task_metrics,
        "emails": {
            "total": email_count,
            "actionable": actionable_count,
            "potentialResponseRequests": response_requests,
            "unanswered": {
                "status": "unknown",
                "reason": "Only incoming inbox data is synced; sent and reply coverage is unavailable.",
            },
        },
        "calendar": {
            "today": sum(event_day(event) == today for event in in_window),
            "upcomingSevenDays": len(upcoming),
            "conflictCount": len(conflicts),
            "conflicts": conflicts[:10],
            "assessedTimedEvents": len(timed_events),
            "eventsWithoutEnd": sum(
                not event.all_day and (event.end is None or event.end <= event.start)
                for event in upcoming
            ),
        },
        "memories": {
            "total": memory_count,
            "goals": goal_count,
            "projects": project_count,
            "highlights": [{
                "id": str(row.id),
                "category": row.category,
                "content": row.content,
                "updatedAt": iso_z(row.updated_at),
            } for row in memory_rows],
        },
        "sources": {
            "gmailLastSyncedAt": iso_z(user.gmail_last_synced_at),
            "calendarLastSyncedAt": iso_z(user.calendar_last_synced_at),
        },
    }
