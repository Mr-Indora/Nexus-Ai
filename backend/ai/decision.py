"""Deterministic task prioritization with explicit limits on available evidence."""
from datetime import date, datetime
from typing import TYPE_CHECKING, Sequence

from timeutil import iso_z, to_local, utcnow

if TYPE_CHECKING:
    from models.models import Task

_URGENCY_RANK = {
    "overdue": 0,
    "due_today": 1,
    "due_soon": 2,
    "upcoming": 3,
    "later": 4,
    "unknown": 5,
}
_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _deadline_band(deadline: datetime | None, now: datetime, today: date) -> str:
    if deadline is None:
        return "unknown"
    if deadline < now:
        return "overdue"
    days = (to_local(deadline).date() - today).days
    if days == 0:
        return "due_today"
    if days <= 3:
        return "due_soon"
    if days < 7:
        return "upcoming"
    return "later"


def _reason(priority: str, deadline_band: str) -> str:
    reasons = {
        "overdue": "Its recorded deadline has passed.",
        "due_today": "Its recorded deadline is today.",
        "due_soon": "Its recorded deadline is within the next three local days.",
        "upcoming": "Its recorded deadline is within the next seven local days.",
        "later": "Its recorded deadline is at least seven local days away.",
        "unknown": "No deadline is recorded, so deadline urgency is unknown.",
    }
    priority_text = f" Stored priority is {priority}." if priority in _PRIORITY_RANK else ""
    return reasons[deadline_band] + priority_text


def build_daily_decision(tasks: Sequence["Task"], now: datetime | None = None) -> dict:
    """Rank existing tasks; do not infer duration, capacity, or dependencies."""
    now = now or utcnow()
    today = to_local(now).date()
    pending = [task for task in tasks if task.status != "completed"]
    classified = [
        (task, _deadline_band(task.deadline, now, today))
        for task in pending
    ]
    classified.sort(key=lambda item: (
        _URGENCY_RANK[item[1]],
        _PRIORITY_RANK.get(item[0].priority, len(_PRIORITY_RANK)),
        item[0].deadline or datetime.max,
        item[0].created_at,
        item[0].id,
    ))

    overdue = sum(band == "overdue" for _, band in classified)
    due_today = sum(band == "due_today" for _, band in classified)
    due_next_seven_days = sum(
        band in {"due_today", "due_soon", "upcoming"}
        for _, band in classified
    )
    priorities = [{
        "taskId": str(task.id),
        "title": task.title,
        "priority": task.priority,
        "deadline": iso_z(task.deadline),
        "deadlineBand": band,
        "riskLevel": (
            "high" if band in {"overdue", "due_today"}
            else "moderate" if band in {"due_soon", "upcoming"}
            else "low" if band == "later"
            else "unknown"
        ),
        "reason": _reason(task.priority, band),
        "source": task.source,
    } for task, band in classified[:5]]
    focus = priorities[0] if priorities else None

    return {
        "generatedAt": iso_z(now),
        "calculationVersion": "deadline-priority-v1",
        "focus": focus,
        "priorities": priorities,
        "workload": {
            "activeTasks": len(pending),
            "overdueTasks": overdue,
            "dueTodayTasks": due_today,
            "dueNextSevenDaysTasks": due_next_seven_days,
            "withoutDeadlines": sum(task.deadline is None for task in pending),
            "estimatedMinutes": None,
            "availableMinutes": None,
            "capacityGapMinutes": None,
            "capacityStatus": "unknown",
            "capacityReason": "Task duration estimates and user availability are not stored.",
            "dependencyStatus": "unavailable",
            "blockedTasks": None,
            "dependencyReason": "Task dependency relationships are not stored.",
        },
        "evidencePolicy": {
            "facts": ["stored task priority", "stored task deadline", "task status"],
            "rankingRule": "deadline window first, then stored priority, deadline, and creation order",
            "limitations": [
                "Priority and deadline values are not independently verified.",
                "No duration or available-work-time estimate is stored, so capacity is unknown.",
                "No task dependencies are stored, so blockers and dependency impact are unavailable.",
                "The deadline-pressure labels are deterministic rules, not calibrated probabilities.",
            ],
        },
    }
