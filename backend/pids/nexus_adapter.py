"""Server-side adapter from Nexus's read-only Gmail sync into PIDS."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from pids.contracts import SourceKind, SourceReference
from pids.email_intelligence import (
    EmailIntelligenceResult,
    EmailIntelligenceService,
    EmailMessageInput,
)
from pids.identity import owner_id_from_principal
from pids.persistence.database import utc_now
from pids.persistence.models import (
    AuditEventRow,
    EmailMessageRow,
    EventCandidateRow,
    KnowledgeSourceRow,
    KnowledgeRecordRow,
    RawRecordRow,
    RecommendationRow,
    SourceSyncStateRow,
    TaskCandidateRow,
    ThreadIntelligenceRow,
)
from pids.persistence.repositories import PidsRepositories
from pids.sources import SourceRegistry
from pids.vault import RawDataVault

MAX_GMAIL_BATCH = 50
_SOURCE_DELETED_LABEL = "PIDS_SOURCE_DELETED"


def record_google_sync_failure(
    session: Session,
    principal: Any,
    *,
    source_kind: SourceKind,
    error_code: str,
) -> None:
    """Persist a safe Gmail/Calendar failure while preserving last success/cursor."""
    if source_kind not in {SourceKind.GMAIL, SourceKind.GOOGLE_CALENDAR}:
        raise ValueError("google sync failure only supports Gmail and Calendar")
    owner_id = owner_id_from_principal(principal)
    display_name = "Gmail" if source_kind == SourceKind.GMAIL else "Google Calendar"
    identity_key = f"nexus-user:{owner_id}"
    source = SourceRegistry(PidsRepositories(session).sources).register(
        owner_id, source_kind, display_name, identity_key, account_ref=f"owner:{owner_id}",
    )
    now = utc_now()
    state = session.get(SourceSyncStateRow, (owner_id, str(source.id)))
    if state is None:
        state = SourceSyncStateRow(
            owner_id=owner_id, source_id=str(source.id), status="error", updated_at=now,
        )
        session.add(state)
    state.status = "error"
    state.last_attempt_at = now
    state.last_error_code = re.sub(r"[^a-z0-9_]", "_", str(error_code).casefold())[:64] or "source_unavailable"
    state.updated_at = now
    session.commit()


def _stable_hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def ingest_gmail_batch(
    session: Session,
    principal: Any,
    messages: Iterable[dict[str, Any]],
    *,
    vault: RawDataVault,
    source_timezone: str = "Asia/Kolkata",
    retention_days: int = 0,
    deleted_ids: Iterable[str] = (),
    history_id: str | None = None,
) -> dict[str, Any]:
    """Analyze a bounded batch; owner identity is derived only from the server principal."""
    owner_id = owner_id_from_principal(principal)
    batch = list(messages)
    deleted_ids = list(dict.fromkeys(str(value) for value in deleted_ids if value))
    if isinstance(retention_days, bool) or not isinstance(retention_days, int) or not 0 <= retention_days <= 3650:
        raise ValueError("retention_days must be between 0 and 3650")
    if len(batch) > MAX_GMAIL_BATCH:
        raise ValueError(f"Gmail batch exceeds the {MAX_GMAIL_BATCH}-message limit")

    repositories = PidsRepositories(session)
    source = SourceRegistry(repositories.sources).register(
        owner_id,
        SourceKind.GMAIL,
        "Gmail",
        f"nexus-user:{owner_id}",
        account_ref=f"owner:{owner_id}",
    )
    now = utc_now()
    sync_state = session.get(SourceSyncStateRow, (owner_id, str(source.id)))
    if sync_state is None:
        sync_state = SourceSyncStateRow(
            owner_id=owner_id, source_id=str(source.id), status="syncing", updated_at=now
        )
        session.add(sync_state)
    sync_state.status = "syncing"
    sync_state.last_attempt_at = now
    sync_state.last_error_code = None
    session.add(AuditEventRow(
        owner_id=owner_id,
        event_type="source_sync_started",
        target_type="source",
        target_id=str(source.id),
        actor_id=owner_id,
        details={"connector": "gmail", "batch_size": len(batch)},
        created_at=now,
    ))
    session.commit()

    processor = EmailIntelligenceService(session, vault=vault)
    known_candidates = session.scalars(select(TaskCandidateRow).where(
        TaskCandidateRow.owner_id == owner_id,
    )).all()
    processed: list[tuple[EmailMessageInput, Any, str, str]] = []
    counts = {"received": len(batch), "processed": 0, "replayed": 0, "failed": 0}
    failures: list[dict[str, str]] = []

    for raw in batch:
        external_id = str(raw.get("gmail_id") or "")
        if not external_id:
            counts["failed"] += 1
            failures.append({"messageId": "unknown", "code": "invalid_external_id"})
            continue
        try:
            message = EmailMessageInput(
                owner_id=owner_id,
                source_id=source.id,
                external_id=external_id,
                text=str(raw.get("body") or ""),
                sender=str(raw.get("sender") or "unknown"),
                subject=str(raw.get("subject") or ""),
                thread_id=str(raw.get("thread_id") or external_id),
                direction="incoming",
                occurred_at=_aware_utc(raw["received_at"]),
                source_timezone=source_timezone,
                retention_expires_at=(now + timedelta(days=retention_days) if retention_days else None),
                source_version=_stable_hash(
                    str(raw.get("sender") or ""),
                    "\x1e".join(str(item) for item in (raw.get("recipients") or [])),
                    str(raw.get("subject") or ""),
                    str(raw.get("body") or ""),
                ),
            )
            prior_version = session.scalar(select(RawRecordRow.id).where(
                RawRecordRow.owner_id == owner_id,
                RawRecordRow.source_id == str(source.id),
                RawRecordRow.external_id == external_id,
                RawRecordRow.source_version == message.source_version,
            ))
            with session.begin_nested():
                analysis, knowledge_id, raw_id = processor.process_email(message, source)
                existing = session.scalar(select(EmailMessageRow).where(
                    EmailMessageRow.owner_id == owner_id,
                    EmailMessageRow.source_id == str(source.id),
                    EmailMessageRow.external_id == external_id,
                ))
                raw_record = repositories.raw_records.get(owner_id, raw_id)
                if existing is None:
                    existing = EmailMessageRow(
                        owner_id=owner_id,
                        source_id=str(source.id),
                        raw_record_id=str(raw_id),
                        external_id=external_id,
                        thread_id=message.thread_id or external_id,
                        sender=message.sender,
                        recipients=list(raw.get("recipients") or []),
                        subject=message.subject,
                        received_at=message.occurred_at,
                        labels=list(raw.get("labels") or []),
                        direction=message.direction,
                        content_ref=raw_record.content_ref,
                        source_version=message.source_version,
                        processed_version=analysis.analysis_version,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(existing)
                else:
                    existing.raw_record_id = str(raw_id)
                    existing.content_ref = raw_record.content_ref
                    existing.thread_id = message.thread_id or external_id
                    existing.sender = message.sender
                    existing.subject = message.subject
                    existing.received_at = message.occurred_at
                    existing.labels = list(raw.get("labels") or [])
                    existing.recipients = list(raw.get("recipients") or [])
                    existing.source_version = message.source_version
                    existing.processed_version = analysis.analysis_version
                    existing.updated_at = now

                current_action_evidence: set[tuple[int, int, str]] = set()
                for action in analysis.actions:
                    key = _stable_hash(str(knowledge_id), str(action.start), str(action.end), action.evidence)
                    identity = (action.start, action.end, action.evidence[:512])
                    current_action_evidence.add(identity)
                    prior_candidate = next((candidate for candidate in known_candidates if (
                        candidate.evidence.get("raw_record_id") == str(raw_id)
                        and candidate.evidence.get("start") == action.start
                        and candidate.evidence.get("end") == action.end
                        and candidate.evidence.get("excerpt") == action.evidence[:512]
                    )), None)
                    if prior_candidate is not None:
                        # A rule-version refresh should improve the existing suggestion,
                        # while preserving the user's prior approve/reject decision.
                        if prior_candidate.review_state == "needs_review":
                            prior_candidate.title = action.title
                            prior_candidate.description = action.description
                            prior_candidate.priority = "high" if analysis.importance in {"critical", "high"} else "normal"
                            prior_candidate.due_date = datetime.fromisoformat(action.due_date).date() if action.due_date else None
                            prior_candidate.knowledge_id = str(knowledge_id)
                            prior_candidate.idempotency_key = key
                            prior_candidate.evidence = {
                                **prior_candidate.evidence,
                                "external_id": external_id,
                                "sender": message.sender,
                                "subject": message.subject,
                                "url": f"https://mail.google.com/mail/u/0/#all/{external_id}",
                            }
                            prior_candidate.updated_at = now
                        continue
                    new_candidate = TaskCandidateRow(
                        owner_id=owner_id,
                        knowledge_id=str(knowledge_id),
                        idempotency_key=key,
                        title=action.title,
                        description=action.description,
                        priority="high" if analysis.importance in {"critical", "high"} else "normal",
                        due_date=(datetime.fromisoformat(action.due_date).date() if action.due_date else None),
                        review_state="needs_review",
                        evidence={
                            "source_id": str(source.id),
                            "raw_record_id": str(raw_id),
                            "start": action.start,
                            "end": action.end,
                            "excerpt": action.evidence[:512],
                            "external_id": external_id,
                            "sender": message.sender,
                            "subject": message.subject,
                            "url": f"https://mail.google.com/mail/u/0/#all/{external_id}",
                        },
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(new_candidate)
                    known_candidates.append(new_candidate)

                # If a rule refresh no longer finds an old suggestion in this
                # same message, remove it from the review queue. Keep decisions
                # the user already made (approved/rejected/dismissed) intact.
                for stale in known_candidates:
                    if (
                        stale.review_state == "needs_review"
                        and stale.evidence.get("raw_record_id") == str(raw_id)
                        and (
                            stale.evidence.get("start"),
                            stale.evidence.get("end"),
                            stale.evidence.get("excerpt"),
                        ) not in current_action_evidence
                    ):
                        stale.review_state = "dismissed"
                        stale.updated_at = now

                recommendation_kinds = []
                if analysis.reply_state != "reply_required":
                    stale_reply_recommendations = session.scalars(select(RecommendationRow).where(
                        RecommendationRow.owner_id == owner_id,
                        RecommendationRow.status == "pending",
                        RecommendationRow.title == "Review requested reply",
                    )).all()
                    for stale in stale_reply_recommendations:
                        if stale.evidence.get("raw_record_id") == str(raw_id):
                            stale.status = "dismissed"
                if analysis.importance in {"critical", "high"}:
                    sender_name = (message.sender or "").split("<", 1)[0].strip()
                    subject = (message.subject or "").strip()
                    reason = (
                        f'{sender_name} sent “{subject}”. It may be worth checking against your plans.'
                        if sender_name and subject else
                        "This email may need your attention. Check whether it applies to you."
                    )
                    recommendation_kinds.append((
                        "important_message",
                        subject[:256] or "An email may need your attention",
                        reason,
                        "Open the email and decide whether to reply, save a task, or leave it.",
                        "high",
                    ))
                if analysis.reply_state == "reply_required":
                    recommendation_kinds.append((
                        "reply_required",
                        "Review requested reply",
                        analysis.reply_reason or "The sender requests a response.",
                        "Review and reply to the sender",
                        "high" if analysis.importance == "critical" else "normal",
                    ))
                for kind, title, reason, action, priority in recommendation_kinds:
                    key = _stable_hash(str(knowledge_id), analysis.analysis_version, kind)
                    if session.scalar(select(RecommendationRow.id).where(
                        RecommendationRow.owner_id == owner_id,
                        RecommendationRow.idempotency_key == key,
                    )):
                        continue
                    session.add(RecommendationRow(
                        owner_id=owner_id,
                        knowledge_id=str(knowledge_id),
                        idempotency_key=key,
                        title=title,
                        description=analysis.summary,
                        reason=reason,
                        priority=priority,
                        confidence=None,
                        suggested_action=action,
                        status="pending",
                        evidence={
                            "source_id": str(source.id),
                            "raw_record_id": str(raw_id),
                            "subject": message.subject,
                            "sender": message.sender,
                        },
                        created_at=now,
                    ))
            session.commit()
            processed.append((message, analysis, str(raw_id), str(knowledge_id)))
            counts["processed"] += 1
            counts["replayed"] += int(prior_version is not None)
        except Exception as exc:
            session.rollback()
            counts["failed"] += 1
            code = getattr(exc, "code", None) or type(exc).__name__.lower()
            failures.append({"messageId": external_id[:128], "code": str(code)[:64]})

    deleted_thread_ids = set()
    deleted_rows = session.scalars(select(EmailMessageRow).where(
        EmailMessageRow.owner_id == owner_id,
        EmailMessageRow.source_id == str(source.id),
        EmailMessageRow.external_id.in_(list(deleted_ids)),
    )).all() if deleted_ids else []
    for row in deleted_rows:
        if _SOURCE_DELETED_LABEL not in row.labels:
            row.labels = [*row.labels, _SOURCE_DELETED_LABEL]
        deleted_thread_ids.add(row.thread_id)
        counts["deleted"] = counts.get("deleted", 0) + 1
    if deleted_rows:
        deleted_raw_ids = {row.raw_record_id for row in deleted_rows}
        pending_tasks = session.scalars(select(TaskCandidateRow).where(
            TaskCandidateRow.owner_id == owner_id,
            TaskCandidateRow.review_state == "needs_review",
        )).all()
        for candidate in pending_tasks:
            if candidate.evidence.get("raw_record_id") in deleted_raw_ids:
                candidate.review_state = "rejected"
                candidate.status = "withdrawn"
                candidate.updated_at = utc_now()
        pending_recommendations = session.scalars(select(RecommendationRow).where(
            RecommendationRow.owner_id == owner_id, RecommendationRow.status == "pending",
        )).all()
        for recommendation in pending_recommendations:
            if recommendation.evidence.get("raw_record_id") in deleted_raw_ids:
                recommendation.status = "dismissed"
        session.add(AuditEventRow(
            owner_id=owner_id, event_type="gmail_source_deletions_reconciled",
            target_type="source", target_id=str(source.id), actor_id=owner_id,
            details={"deleted_count": len(deleted_rows)}, created_at=utc_now(),
        ))
        for thread_id in deleted_thread_ids:
            thread = session.scalar(select(ThreadIntelligenceRow).where(
                ThreadIntelligenceRow.owner_id == owner_id,
                ThreadIntelligenceRow.source_id == str(source.id),
                ThreadIntelligenceRow.thread_id == thread_id,
            ))
            if thread:
                thread.analysis = {
                    "summary": "A source message was deleted from Gmail; review this thread again.",
                    "reply_state": "unknown", "deadlines": [], "pending_actions": [],
                    "source_deletions_pending_review": True,
                }
                thread.updated_at = utc_now()

    threads: dict[str, list[tuple[EmailMessageInput, Any, str, str]]] = defaultdict(list)
    for item in processed:
        threads[item[0].thread_id or item[0].external_id].append(item)
    for thread_id, items in threads.items():
        try:
            processed_by_raw = {item[2]: item for item in items}
            stored_messages = session.scalars(select(EmailMessageRow).where(
                EmailMessageRow.owner_id == owner_id,
                EmailMessageRow.source_id == str(source.id),
                EmailMessageRow.thread_id == thread_id,
            ).order_by(EmailMessageRow.received_at)).all()
            analyzed_messages = []
            for email_row in stored_messages:
                current = processed_by_raw.get(email_row.raw_record_id)
                if current is not None:
                    message, analysis, raw_id, _ = current
                else:
                    raw_row = session.scalar(select(RawRecordRow).where(
                        RawRecordRow.owner_id == owner_id,
                        RawRecordRow.source_id == str(source.id),
                        RawRecordRow.id == email_row.raw_record_id,
                    ))
                    knowledge_row = session.scalar(
                        select(KnowledgeRecordRow)
                        .join(KnowledgeSourceRow, and_(
                            KnowledgeSourceRow.owner_id == KnowledgeRecordRow.owner_id,
                            KnowledgeSourceRow.knowledge_id == KnowledgeRecordRow.id,
                        ))
                        .where(
                            KnowledgeSourceRow.owner_id == owner_id,
                            KnowledgeSourceRow.source_id == str(source.id),
                            KnowledgeSourceRow.raw_record_id == email_row.raw_record_id,
                            KnowledgeRecordRow.record_type == "email_intelligence",
                        )
                        .order_by(KnowledgeRecordRow.created_at.desc())
                    )
                    if raw_row is None or knowledge_row is None:
                        raise ValueError("thread source evidence is unavailable")
                    payload = vault.read(owner_id, raw_row.content_sha256)
                    message = EmailMessageInput(
                        owner_id=owner_id,
                        source_id=source.id,
                        external_id=email_row.external_id,
                        text=payload.decode(raw_row.character_encoding or "utf-8", errors="strict"),
                        sender=email_row.sender,
                        subject=email_row.subject,
                        thread_id=email_row.thread_id,
                        direction=email_row.direction,
                        occurred_at=email_row.received_at,
                        source_timezone=raw_row.source_timezone or source_timezone,
                        source_version=raw_row.source_version,
                    )
                    analysis = EmailIntelligenceResult.model_validate(knowledge_row.data)
                    raw_id = email_row.raw_record_id
                analyzed_messages.append((
                    message,
                    analysis,
                    SourceReference(
                        owner_id=owner_id,
                        source_id=source.id,
                        raw_record_id=raw_id,
                        evidence_location="email_thread_message",
                    ),
                ))
            thread_result, _ = processor.process_thread_analyses(source, tuple(analyzed_messages))
            row = session.scalar(select(ThreadIntelligenceRow).where(
                ThreadIntelligenceRow.owner_id == owner_id,
                ThreadIntelligenceRow.source_id == str(source.id),
                ThreadIntelligenceRow.thread_id == thread_id,
            ))
            now = utc_now()
            if row is None:
                row = ThreadIntelligenceRow(
                    owner_id=owner_id,
                    source_id=str(source.id),
                    thread_id=thread_id,
                    analysis=thread_result.model_dump(mode="json", exclude={"source_refs"}),
                    analysis_version=thread_result.analysis_version,
                    latest_message_at=thread_result.latest_message_at,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.analysis = thread_result.model_dump(mode="json", exclude={"source_refs"})
                row.analysis_version = thread_result.analysis_version
                row.latest_message_at = thread_result.latest_message_at
                row.updated_at = now
            session.commit()
        except Exception as exc:
            session.rollback()
            counts["failed"] += len(items)
            failures.append({"messageId": thread_id[:128], "code": (getattr(exc, "code", None) or type(exc).__name__.lower())[:64]})

    finished = utc_now()
    sync_state = session.get(SourceSyncStateRow, (owner_id, str(source.id)))
    if sync_state is not None:
        sync_state.status = "partial" if counts["failed"] else "idle"
        sync_state.last_error_code = "item_processing_failed" if counts["failed"] else None
        if not counts["failed"]:
            sync_state.last_success_at = finished
            if history_id:
                sync_state.cursor = history_id[:2048]
        sync_state.updated_at = finished
    session.add(AuditEventRow(
        owner_id=owner_id,
        event_type="source_sync_finished",
        target_type="source",
        target_id=str(source.id),
        actor_id=owner_id,
        details={**counts, "failure_count": len(failures)},
        created_at=finished,
    ))
    session.commit()
    return {**counts, "sourceId": str(source.id), "failures": failures}


def ingest_calendar_batch(
    session: Session,
    principal: Any,
    events: Iterable[dict[str, Any]],
    *,
    vault: RawDataVault,
    source_timezone: str = "Asia/Kolkata",
    cancelled_ids: Iterable[str] = (),
    retention_days: int = 0,
) -> dict[str, Any]:
    """Persist read-only Calendar snapshots as evidence-backed event candidates."""
    owner_id = owner_id_from_principal(principal)
    batch = list(events)
    if isinstance(retention_days, bool) or not isinstance(retention_days, int) or not 0 <= retention_days <= 3650:
        raise ValueError("retention_days must be between 0 and 3650")
    if len(batch) > MAX_GMAIL_BATCH:
        raise ValueError(f"Calendar batch exceeds the {MAX_GMAIL_BATCH}-event limit")
    repositories = PidsRepositories(session)
    source = SourceRegistry(repositories.sources).register(
        owner_id, SourceKind.GOOGLE_CALENDAR, "Google Calendar",
        f"nexus-user:{owner_id}", account_ref=f"owner:{owner_id}",
    )
    session.commit()
    counts = {"received": len(batch), "processed": 0, "replayed": 0, "failed": 0}
    failures = []
    now = utc_now()
    for event in batch:
        external_id = str(event.get("google_id") or "")
        if not external_id:
            counts["failed"] += 1
            failures.append({"eventId": "unknown", "code": "invalid_external_id"})
            continue
        payload = json.dumps(event, sort_keys=True, default=str, separators=(",", ":")).encode()
        expires_at = now + timedelta(days=retention_days) if retention_days else None
        digest, content_ref, size = vault.store(
            owner_id, payload, retention_expires_at=expires_at,
        )
        version = hashlib.sha256(payload).hexdigest()
        key = _stable_hash(str(source.id), external_id, version)
        try:
            existing = session.scalar(select(EventCandidateRow).where(
                EventCandidateRow.owner_id == owner_id,
                EventCandidateRow.idempotency_key == key,
            ))
            if existing:
                counts["replayed"] += 1
                counts["processed"] += 1
                continue
            prior_candidates = session.scalars(select(EventCandidateRow).where(
                EventCandidateRow.owner_id == owner_id,
                EventCandidateRow.review_state == "needs_review",
            )).all()
            for prior in prior_candidates:
                if prior.evidence.get("externalId") == external_id:
                    prior.review_state = "superseded"
                    prior.updated_at = now
            raw_id = str(UUID(hex=_stable_hash("calendar-raw", str(owner_id), str(source.id), external_id, version)[:32]))
            raw = RawRecordRow(
                id=raw_id, owner_id=owner_id, source_id=str(source.id),
                external_id=external_id, content_sha256=digest,
                content_ref=content_ref, payload_size_bytes=size,
                content_type="application/json", character_encoding="utf-8",
                retention_expires_at=expires_at, source_timezone=source_timezone,
                occurred_at=_aware_utc(event["start"]), ingested_at=now,
                source_version=version, created_at=now, updated_at=now,
            )
            session.add(raw)
            session.flush()
            knowledge_id = str(UUID(hex=_stable_hash("calendar-knowledge", str(owner_id), key)[:32]))
            knowledge = KnowledgeRecordRow(
                id=knowledge_id, owner_id=owner_id, record_type="calendar_event",
                data={"title": str(event.get("title") or "(No title)"),
                      "start": _aware_utc(event["start"]).isoformat(),
                      "end": _aware_utc(event["end"]).isoformat() if event.get("end") else None,
                      "all_day": bool(event.get("all_day")),
                      "location": str(event.get("location") or ""),
                      "meet_link": str(event.get("meet_link") or "")},
                idempotency_key=key, processing_version="calendar-snapshot-v1",
                verification="unverified", confidence=None,
                lifecycle_status="candidate", version=1,
                created_at=now, updated_at=now,
            )
            session.add(knowledge)
            session.add(KnowledgeSourceRow(
                owner_id=owner_id, knowledge_id=knowledge_id,
                source_id=str(source.id), raw_record_id=raw_id,
                evidence_location="google_calendar_event",
            ))
            session.flush()
            start = event["start"]
            all_day = bool(event.get("all_day"))
            session.add(EventCandidateRow(
                owner_id=owner_id, knowledge_id=knowledge_id,
                idempotency_key=key, title=str(event.get("title") or "(No title)")[:256],
                start_at=None if all_day else _aware_utc(start),
                end_at=None if all_day or not event.get("end") else _aware_utc(event["end"]),
                event_date=start.date() if all_day else None,
                source_timezone=source_timezone, all_day=all_day,
                review_state="needs_review",
                evidence={"sourceId": str(source.id), "rawRecordId": raw_id,
                          "externalId": external_id, "contentRef": content_ref},
                created_at=now, updated_at=now,
            ))
            session.commit()
            counts["processed"] += 1
        except Exception as exc:
            session.rollback()
            counts["failed"] += 1
            failures.append({"eventId": external_id[:128], "code": (getattr(exc, "code", None) or type(exc).__name__).lower()[:64]})
    cancelled_ids = list(dict.fromkeys(str(value) for value in cancelled_ids if value))
    if cancelled_ids:
        candidates = session.scalars(select(EventCandidateRow).where(
            EventCandidateRow.owner_id == owner_id,
            EventCandidateRow.review_state != "cancelled",
        )).all()
        cancelled = set(cancelled_ids)
        count = 0
        for candidate in candidates:
            if candidate.evidence.get("externalId") in cancelled:
                candidate.review_state = "cancelled"
                candidate.updated_at = utc_now()
                count += 1
        if count:
            session.add(AuditEventRow(
                owner_id=owner_id, event_type="calendar_cancellations_reconciled",
                target_type="source", target_id=str(source.id), actor_id=owner_id,
                details={"cancelled_count": count}, created_at=utc_now(),
            ))
            session.commit()
        counts["cancelled"] = count
    return {**counts, "sourceId": str(source.id), "failures": failures}
