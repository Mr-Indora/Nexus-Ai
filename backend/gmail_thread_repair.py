"""User-scoped repair for Gmail thread metadata using readonly message lookups."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from integrations.google_api import GoogleAPIError
from models.models import Email, ThreadIntelligence, User, utcnow


def _rebuild_thread(db: Session, user_id: int, thread_id: str) -> None:
    messages = db.scalars(select(Email).where(
        Email.user_id == user_id,
        Email.thread_id == thread_id,
    ).order_by(Email.received_at)).all()
    if not messages:
        return
    combined = "\n".join(f"{m.subject}\n{m.body}" for m in messages)
    lower = combined.lower()
    action_rows = [m for m in messages if m.is_actionable]
    thread = db.scalar(select(ThreadIntelligence).where(
        ThreadIntelligence.user_id == user_id,
        ThreadIntelligence.thread_id == thread_id,
    ))
    if not thread:
        thread = ThreadIntelligence(user_id=user_id, thread_id=thread_id)
        db.add(thread)
    thread.summary = messages[-1].subject[:255]
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


def repair_missing_thread_ids(db: Session, user: User, client) -> dict:
    """Backfill NULL thread IDs for one non-demo user and rebuild affected threads."""
    if user.is_demo:
        raise ValueError("Thread repair is only available for real Google users")

    rows = db.scalars(select(Email).where(
        Email.user_id == user.id,
        Email.thread_id.is_(None),
    ).order_by(Email.id)).all()
    updates: list[tuple[Email, str]] = []
    for row in rows:
        message = client.get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{row.gmail_id}",
            {"format": "metadata"},
        )
        thread_id = message.get("threadId")
        if not isinstance(thread_id, str) or not thread_id:
            raise GoogleAPIError(502, f"Gmail message {row.gmail_id} has no valid threadId")
        updates.append((row, thread_id))

    affected = set()
    for row, thread_id in updates:
        row.thread_id = thread_id
        affected.add(thread_id)

    existing_thread_ids = set(db.scalars(select(Email.thread_id).where(
        Email.user_id == user.id,
        Email.thread_id.is_not(None),
    )).all())
    stored_thread_ids = set(db.scalars(select(ThreadIntelligence.thread_id).where(
        ThreadIntelligence.user_id == user.id,
    )).all())
    missing_thread_records = existing_thread_ids - stored_thread_ids
    affected.update(missing_thread_records)

    for thread_id in affected:
        _rebuild_thread(db, user.id, thread_id)
    db.commit()
    return {
        "recordsFound": len(rows),
        "recordsRepaired": len(updates),
        "threadsRebuilt": len(affected),
    }
