"""Owner-scoped, deterministic decision metrics over persisted PIDS records."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pids.persistence.models import EventCandidateRow, RecommendationRow, TaskCandidateRow, ThreadIntelligenceRow


def _local_due_date(task: TaskCandidateRow, zone: ZoneInfo) -> date | None:
    if task.due_date is not None:
        return task.due_date
    if task.due_at is not None:
        instant = task.due_at
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        return instant.astimezone(zone).date()
    return None


def build_pids_analytics(
    tasks: Iterable[TaskCandidateRow],
    threads: Iterable[ThreadIntelligenceRow],
    recommendations: Iterable[RecommendationRow],
    events: Iterable[EventCandidateRow] = (),
    *,
    now: datetime | None = None,
    timezone_name: str = "Asia/Kolkata",
) -> dict:
    """Rank only recorded dates, review state and thread conclusions; omit estimates."""
    try:
        zone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError):
        zone = ZoneInfo("UTC")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("analytics reference time must include a timezone")
    today = now.astimezone(zone).date()
    task_rows = tuple(tasks)
    thread_rows = tuple(threads)
    recommendation_rows = tuple(recommendations)
    event_rows = tuple(events)
    active = [
        task for task in task_rows
        if task.status != "completed" and task.review_state != "rejected"
    ]
    due_dates = {task.id: _local_due_date(task, zone) for task in active}
    overdue = [task for task in active if due_dates[task.id] is not None and due_dates[task.id] < today]
    due_today = [task for task in active if due_dates[task.id] == today]
    due_next_seven = [
        task for task in active
        if due_dates[task.id] is not None and today <= due_dates[task.id] <= today + timedelta(days=7)
    ]
    awaiting_reply = [
        thread for thread in thread_rows
        if thread.analysis.get("reply_state") == "reply_required"
    ]
    changed_dates = [
        thread for thread in thread_rows
        if thread.analysis.get("superseded_deadlines")
    ]
    timed_events = [
        event for event in event_rows
        if event.review_state != "rejected" and not event.all_day
        and event.start_at is not None and event.end_at is not None
        and event.end_at > event.start_at
    ]
    conflicts = []
    for index, first in enumerate(timed_events):
        for second in timed_events[index + 1:]:
            if first.start_at < second.end_at and second.start_at < first.end_at:
                conflicts.append((first, second))

    signals = []
    for task in overdue:
        signals.append({
            "kind": "overdue_task",
            "priority": "high",
            "title": task.title,
            "reason": f"The recorded candidate date {due_dates[task.id].isoformat()} is before today ({today.isoformat()}).",
            "evidence": {"taskCandidateId": task.id, "knowledgeId": task.knowledge_id},
        })
    for task in due_today:
        signals.append({
            "kind": "task_due_today",
            "priority": "high",
            "title": task.title,
            "reason": f"The recorded candidate date is today ({today.isoformat()}).",
            "evidence": {"taskCandidateId": task.id, "knowledgeId": task.knowledge_id},
        })
    for thread in awaiting_reply:
        signals.append({
            "kind": "reply_requested",
            "priority": "normal",
            "title": str(thread.analysis.get("summary") or "Review requested reply"),
            "reason": "The latest thread analysis says a response is required.",
            "evidence": {
                "threadId": thread.thread_id,
                "threadRecordId": thread.id,
                "latestMessageAt": thread.latest_message_at.isoformat() if thread.latest_message_at else None,
            },
        })
    for thread in changed_dates:
        signals.append({
            "kind": "date_changed",
            "priority": "normal",
            "title": str(thread.analysis.get("summary") or "Review changed date"),
            "reason": "A newer message superseded a previously mentioned date.",
            "evidence": {
                "threadId": thread.thread_id,
                "currentDates": thread.analysis.get("deadlines", []),
                "supersededDates": thread.analysis.get("superseded_deadlines", []),
            },
        })
    for first, second in conflicts:
        signals.append({
            "kind": "calendar_conflict",
            "priority": "high",
            "title": f"Possible event overlap: {first.title} / {second.title}",
            "reason": "The recorded event time ranges overlap.",
            "evidence": {"eventCandidateIds": [first.id, second.id]},
        })
    signals.sort(key=lambda item: (0 if item["priority"] == "high" else 1, item["kind"], item["title"].casefold()))

    return {
        "calculationVersion": "pids-deterministic-analytics-v1",
        "generatedAt": now.astimezone(timezone.utc).isoformat(),
        "timezone": timezone_name if zone.key == timezone_name else "UTC",
        "counts": {
            "activeTaskCandidates": len(active),
            "needsTaskReview": sum(task.review_state == "needs_review" for task in active),
            "overdueByRecordedDate": len(overdue),
            "dueTodayByRecordedDate": len(due_today),
            "dueWithinSevenDays": len(due_next_seven),
            "awaitingReplies": len(awaiting_reply),
            "threadsWithSupersededDates": len(changed_dates),
            "pendingRecommendations": sum(item.status == "pending" for item in recommendation_rows),
            "calendarEventCandidates": len(event_rows),
            "calendarOverlappingPairs": len(conflicts),
        },
        "signals": signals[:50],
        "limitations": [
            "Date-only candidates remain dates and are compared in the selected timezone.",
            "No duration or personal availability estimate is stored; capacity is unknown.",
            "Calendar conflicts compare only timed events with recorded start and end values; all-day events are excluded.",
            "Priority labels are deterministic rule outputs and are not calibrated probabilities.",
        ],
    }
