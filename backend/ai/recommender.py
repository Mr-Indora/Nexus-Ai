"""1-2 line 'what should I do now' recommendation from tasks + today's calendar."""
import hashlib
import logging
import time

import httpx

from config import settings
from ai.gemini_client import generate_text
from timeutil import human_due, to_local, utcnow

_cache: dict[str, tuple[float, dict]] = {}
TTL = 600
logger = logging.getLogger(__name__)


def _task_line(t) -> str:
    return f"{t.title} [{t.priority}, {t.category}, due {human_due(t.deadline)}]"


def _event_line(e) -> str:
    when = to_local(e["start"]).strftime("%I:%M %p").lstrip("0") if not e["all_day"] else "all day"
    return f"{e['title']} at {when}" + (" (Meet)" if e["meet_link"] else "")


def rule_based(pending: list, today_events: list[dict]) -> str:
    if not pending and not today_events:
        return "You're all clear — nothing urgent today. Good time to get ahead on a bigger task."
    parts = []
    if pending:
        top = pending[0]
        due = human_due(top.deadline)
        parts.append(
            f"Start with “{top.title}” — it's {due}." if due in ("overdue", "no deadline") or top.deadline is None
            else f"Start with “{top.title}” — due {due}."
        )
    upcoming = [e for e in today_events if e["start"] >= utcnow() and not e["all_day"]]
    if upcoming:
        nxt = upcoming[0]
        parts.append(f"Your next commitment is “{nxt['title']}” at {to_local(nxt['start']).strftime('%I:%M %p').lstrip('0')}, so use the time before it.")
    elif len(pending) > 1:
        high = sum(1 for t in pending if t.priority == "high")
        parts.append(f"{len(pending) - 1} more task(s) waiting ({high} high priority overall).")
    return " ".join(parts)


def _gemini(pending, today_events) -> str:
    prompt = (
        "You are a concise productivity planner for a college student. Given the data, write a 1-2 sentence "
        "recommendation on what to do next and why. Plain text, no markdown, no greetings.\n\n"
        f"Pending tasks (most urgent first):\n" + "\n".join(f"- {_task_line(t)}" for t in pending[:6])
        + "\n\nToday's calendar:\n" + ("\n".join(f"- {_event_line(e)}" for e in today_events[:6]) or "- nothing scheduled")
    )
    return generate_text(prompt, max_output_tokens=120, timeout=30)


def recommend(pending: list, today_events: list[dict], allow_cloud: bool = False) -> dict:
    key = hashlib.sha1(
        ("|".join(
            f"{t.id}:{t.status}:{t.deadline}:{t.priority}:{t.title}:{t.category}"
            for t in pending[:6]
        )
         + "#" + "|".join(
             f"{e.get('id', '')}:{e['title']}:{e['start']}:{e['all_day']}:{e['meet_link']}"
             for e in today_events
         )
         + f"#cloud:{allow_cloud}").encode()
    ).hexdigest()
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    evidence = []
    if pending:
        task = pending[0]
        evidence.append({
            "type": "task", "id": str(task.id), "title": task.title,
            "detail": f"{task.priority} priority · {human_due(task.deadline)}",
        })
    upcoming = [event for event in today_events if event["start"] >= utcnow() and not event["all_day"]]
    if upcoming:
        event = upcoming[0]
        evidence.append({
            "type": "calendar", "id": str(event.get("id", "")), "title": event["title"],
            "detail": to_local(event["start"]).strftime("%I:%M %p").lstrip("0"),
        })
    out = {"text": rule_based(pending, today_events), "engine": "rules", "evidence": evidence}
    if allow_cloud and settings.GEMINI_API_KEY and (pending or today_events):
        try:
            out = {"text": _gemini(pending, today_events), "engine": "gemini", "evidence": evidence}
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning("Gemini recommendation unavailable; using rules (%s)", type(exc).__name__)
    _cache[key] = (time.time(), out)
    return out
