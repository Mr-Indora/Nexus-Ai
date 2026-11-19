"""Owner-scoped persistence for read-only college and Classroom source items."""

from __future__ import annotations

import hashlib
import json
import re
from calendar import month_abbr, month_name
from datetime import date, datetime, timezone
from typing import Any, Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai.academic import summarize_academic_batch
from config import settings
from pids.contracts import SourceKind
from pids.identity import owner_id_from_principal
from pids.persistence.database import utc_now
from pids.persistence.models import (
    KnowledgeRecordRow,
    KnowledgeSourceRow,
    RecommendationRow,
    RawRecordRow,
    SourceSyncStateRow,
    TaskCandidateRow,
)
from pids.persistence.repositories import PidsRepositories
from pids.sources import SourceRegistry
from pids.vault import RawDataVault

_DEADLINE_CUE = re.compile(r"\b(?:last\s+date|deadline|due\s+date|due\s+by|submit\s+by|apply\s+by|pay\s+by|by|before|upto|up\s+to)\b|अंतिम\s*(?:तिथि|दिनांक)|तिथि\s*तक|दिनांक\s*तक|जमा\s*करने\s*की\s*(?:तिथि|अंतिम\s*तिथि)", re.I)
_ACTION_CUE = re.compile(r"\b(?:submit|apply|register|enroll|fill|pay|upload|attend|complete|prepare|assignment|coursework|form)\b|जमा\s*करें|भरे|भरें|आवेदन|पंजीकरण|शुल्क|असाइनमेंट|परीक्षा", re.I)
_MONTH_LOOKUP = {
    name.casefold(): index for index, name in enumerate(month_name) if name
}
_MONTH_LOOKUP.update({
    name.casefold(): index for index, name in enumerate(month_abbr) if name
})
_MONTH_PATTERN = "|".join(sorted(_MONTH_LOOKUP, key=len, reverse=True))
_DATE_PATTERNS = (
    re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b"),
    re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d{2})\b"),
    re.compile(r"\b(\d{1,2})\s+(" + _MONTH_PATTERN + r")\s+(20\d{2})\b", re.I),
    re.compile(r"\b(" + _MONTH_PATTERN + r")\s+(\d{1,2}),?\s+(20\d{2})\b", re.I),
)


def _deadline_priority(due_date: date | None, *, today: date | None = None) -> str:
    """Only mark an academic action high when its verified date is still near."""
    if due_date is None:
        return "normal"
    local_today = today or datetime.now(ZoneInfo("Asia/Kolkata")).date()
    days_until_due = (due_date - local_today).days
    return "high" if -3 <= days_until_due <= 3 else "normal"


def _deadline_date(text: str) -> date | None:
    text = text.translate(str.maketrans("०१२३४५६७८९", "0123456789"))
    for sentence in re.split(r"(?<=[.!?])\s+|[\r\n]+", text):
        if not _DEADLINE_CUE.search(sentence):
            continue
        for pattern in _DATE_PATTERNS:
            match = pattern.search(sentence)
            if not match:
                continue
            groups = match.groups()
            try:
                if pattern is _DATE_PATTERNS[0]:
                    year, month, day = map(int, groups)
                elif pattern is _DATE_PATTERNS[1]:
                    day, month, year = map(int, groups)
                    # This source is an Indian college site; numeric dates use day/month/year.
                elif pattern is _DATE_PATTERNS[2]:
                    day, month_name, year = groups
                    month = _MONTH_LOOKUP[month_name.casefold()]
                    day, year = int(day), int(year)
                else:
                    month_name, day, year = groups
                    month = _MONTH_LOOKUP[month_name.casefold()]
                    day, year = int(day), int(year)
                return date(year, month, day)
            except (ValueError, IndexError):
                continue
    return None


def _digest(*values: str) -> str:
    return hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()


def _short(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def record_academic_sync_failure(
    session: Session,
    principal: Any,
    *,
    source_kind: SourceKind,
    source_name: str,
    identity_key: str,
    error_code: str,
) -> None:
    """Persist a bounded, owner-scoped connector failure for truthful status UI."""
    owner_id = owner_id_from_principal(principal)
    repository = PidsRepositories(session)
    source = SourceRegistry(repository.sources).register(
        owner_id, source_kind, source_name, identity_key, account_ref=identity_key,
    )
    now = utc_now()
    state = session.get(SourceSyncStateRow, (owner_id, str(source.id)))
    if state is None:
        state = SourceSyncStateRow(owner_id=owner_id, source_id=str(source.id), status="error", updated_at=now)
        session.add(state)
    state.status = "error"
    state.last_attempt_at = now
    state.last_error_code = _short(error_code, 64) or "source_unavailable"
    state.updated_at = now
    session.commit()


def ingest_academic_items(
    session: Session,
    principal: Any,
    items: Iterable[dict[str, Any]],
    *,
    source_kind: SourceKind,
    source_name: str,
    identity_key: str,
    vault: RawDataVault,
    retention_days: int = 0,
) -> dict[str, int | str]:
    """Persist academic items idempotently; all extracted actions stay reviewable."""
    if source_kind not in {SourceKind.COLLEGE_NOTICES, SourceKind.GOOGLE_CLASSROOM}:
        raise ValueError("academic adapter only supports college notice and Classroom sources")
    if not 0 <= retention_days <= 3650:
        raise ValueError("retention_days must be between 0 and 3650")
    owner_id = owner_id_from_principal(principal)
    repository = PidsRepositories(session)
    source = SourceRegistry(repository.sources).register(
        owner_id, source_kind, source_name, identity_key, account_ref=identity_key,
    )
    now = utc_now()
    batch = list(items)
    consented = bool(
        getattr(principal, "external_ai_consent", False)
        and settings.LOCAL_LLM_MODE != "local-only"
    )
    eligible_for_ai: list[dict[str, Any]] = []
    if consented:
        for candidate in batch:
            external_id = _short(candidate.get("external_id"), 512)
            version = _short(candidate.get("source_version") or external_id, 256)
            if not external_id or not version:
                continue
            key = _digest("academic-knowledge-v1", _digest(str(owner_id), str(source.id), external_id, version))
            existing = session.scalar(select(KnowledgeRecordRow).where(
                KnowledgeRecordRow.owner_id == owner_id,
                KnowledgeRecordRow.idempotency_key == key,
            ))
            if existing and existing.data.get("analysis_method") == "gemini_academic_summary_v1":
                continue
            if existing and existing.data.get("ai_attempted_at"):
                try:
                    attempted = datetime.fromisoformat(str(existing.data["ai_attempted_at"]))
                    attempted = attempted.replace(tzinfo=timezone.utc) if attempted.tzinfo is None else attempted
                    if (now - attempted.astimezone(timezone.utc)).total_seconds() < 6 * 60 * 60:
                        continue
                except ValueError:
                    pass
            eligible_for_ai.append(candidate)
    ai_summaries, ai_status = summarize_academic_batch(
        eligible_for_ai[:5], consented=consented, owner_id=owner_id,
    )
    attempted_at = now.isoformat() if ai_status in {"provider_unavailable", "invalid_response"} else None
    sync_state = session.get(SourceSyncStateRow, (owner_id, str(source.id)))
    if sync_state is None:
        sync_state = SourceSyncStateRow(owner_id=owner_id, source_id=str(source.id), status="syncing", updated_at=now)
        session.add(sync_state)
    sync_state.status = "syncing"
    sync_state.last_attempt_at = now
    sync_state.last_error_code = None

    counts = {"received": 0, "processed": 0, "replayed": 0, "tasks": 0, "notifications": 0}
    for item in batch:
        counts["received"] += 1
        external_id = _short(item.get("external_id"), 512)
        version = _short(item.get("source_version") or external_id, 256)
        title = _short(item.get("title") or "New academic update", 512)
        body = _short(item.get("body"), 100_000)
        url = _short(item.get("url"), 2048)
        if not external_id or not version or not title or not url:
            continue
        payload = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        identity = _digest(str(owner_id), str(source.id), external_id, version)
        knowledge_key = _digest("academic-knowledge-v1", identity)
        existing_knowledge = session.scalar(select(KnowledgeRecordRow).where(
            KnowledgeRecordRow.owner_id == owner_id,
            KnowledgeRecordRow.idempotency_key == knowledge_key,
        ))
        if existing_knowledge:
            ai_summary = ai_summaries.get(external_id)
            if ai_summary:
                existing_knowledge.data = {
                    **existing_knowledge.data,
                    "topic": ai_summary["topic"],
                    "summary": ai_summary["summary"],
                    "analysis_method": "gemini_academic_summary_v1",
                    "ai_attempted_at": now.isoformat(),
                }
                existing_knowledge.updated_at = now
            elif attempted_at:
                existing_knowledge.data = {**existing_knowledge.data, "ai_attempted_at": attempted_at}
                existing_knowledge.updated_at = now
            if item.get("author"):
                existing_knowledge.data = {**existing_knowledge.data, "author": _short(item.get("author"), 200)}
                existing_knowledge.updated_at = now
            try:
                existing_due = date.fromisoformat(str(existing_knowledge.data.get("due_date") or "")[:10])
            except ValueError:
                existing_due = None
            recalculated_priority = _deadline_priority(existing_due)
            task_key = _digest("academic-task-v1", str(owner_id), str(source.id), external_id)
            existing_task = session.scalar(select(TaskCandidateRow).where(
                TaskCandidateRow.owner_id == owner_id,
                TaskCandidateRow.idempotency_key == task_key,
            ))
            if existing_task and existing_task.review_state == "needs_review":
                existing_task.priority = recalculated_priority
                existing_task.updated_at = now
                ai_action = bool(
                    ai_summary and ai_summary["needs_action"]
                    and _ACTION_CUE.search(f"{existing_knowledge.data.get('title', '')} {item.get('body', '')}")
                    and ai_summary["action_title"]
                )
                if ai_summary:
                    existing_task.description = str(ai_summary["summary"])[:4000]
                    if ai_action:
                        existing_task.title = str(ai_summary["action_title"])[:256]
            recommendation_key = _digest("academic-notification-v1", str(owner_id), str(source.id), external_id)
            existing_recommendation = session.scalar(select(RecommendationRow).where(
                RecommendationRow.owner_id == owner_id,
                RecommendationRow.idempotency_key == recommendation_key,
            ))
            if existing_recommendation and existing_recommendation.status == "pending":
                existing_recommendation.priority = recalculated_priority
                if ai_summary:
                    existing_recommendation.description = str(ai_summary["summary"])[:4000]
            counts["replayed"] += 1
            continue

        try:
            content_hash, content_ref, payload_size = vault.store(owner_id, payload, encrypt=True)
            raw = RawRecordRow(
                id=str(uuid4()), owner_id=owner_id, source_id=str(source.id),
                external_id=external_id, source_version=version,
                content_sha256=content_hash, content_ref=content_ref,
                payload_size_bytes=payload_size, content_type="application/json",
                character_encoding="utf-8",
                retention_expires_at=(datetime.fromtimestamp(now.timestamp() + retention_days * 86400, timezone.utc)
                                      if retention_days else None),
                source_timezone="Asia/Kolkata", occurred_at=item.get("published_at"),
                ingested_at=now, created_at=now, updated_at=now,
            )
            session.add(raw)
            session.flush()

            raw_text = f"{title}\n{body}".strip()
            due_date = item.get("due_date")
            if isinstance(due_date, str):
                try:
                    due_date = date.fromisoformat(due_date[:10])
                except ValueError:
                    due_date = None
            if due_date is None and source_kind == SourceKind.COLLEGE_NOTICES:
                due_date = _deadline_date(raw_text)
            record_type = _short(item.get("record_type"), 64) or "academic_notice"
            ai_summary = ai_summaries.get(external_id)
            summary_text = str(ai_summary["summary"]) if ai_summary else (body[:1200] if body else title)
            knowledge = KnowledgeRecordRow(
                id=str(uuid4()), owner_id=owner_id, record_type=record_type,
                data={
                    "title": title, "summary": summary_text,
                    "topic": str(ai_summary["topic"]) if ai_summary else "",
                    "url": url, "course": _short(item.get("course"), 200),
                    "author": _short(item.get("author"), 200),
                    "category": _short(item.get("category") or item.get("kind"), 120),
                    "notice_number": _short(item.get("notice_number"), 80),
                    "published_at": item.get("published_at").isoformat() if isinstance(item.get("published_at"), datetime) else None,
                    "due_date": due_date.isoformat() if isinstance(due_date, date) else None,
                    "content_extracted": bool(item.get("content_extracted", True)),
                    "analysis_method": "gemini_academic_summary_v1" if ai_summary else "deterministic_academic_rules_v1",
                    "ai_attempted_at": now.isoformat() if ai_summary or attempted_at else None,
                },
                idempotency_key=knowledge_key, processing_version="academic-v1",
                verification="needs_review", lifecycle_status="candidate", version=1,
                created_at=now, updated_at=now,
            )
            session.add(knowledge)
            session.add(KnowledgeSourceRow(
                owner_id=owner_id, knowledge_id=knowledge.id, source_id=str(source.id),
                raw_record_id=raw.id, evidence_location="title_and_source_link",
            ))

            ai_action = bool(
                ai_summary and ai_summary["needs_action"]
                and _ACTION_CUE.search(raw_text) and ai_summary["action_title"]
            )
            has_action = ai_action or (
                source_kind == SourceKind.GOOGLE_CLASSROOM and record_type == "classroom_coursework"
            ) or (
                source_kind == SourceKind.GOOGLE_CLASSROOM and record_type == "classroom_announcement"
                and bool(_ACTION_CUE.search(raw_text))
            ) or (
                source_kind == SourceKind.GOOGLE_CLASSROOM and record_type == "classroom_material"
                and bool(_ACTION_CUE.search(raw_text))
            ) or (
                source_kind == SourceKind.COLLEGE_NOTICES and bool(_ACTION_CUE.search(raw_text))
            )
            if has_action:
                task_key = _digest("academic-task-v1", str(owner_id), str(source.id), external_id)
                existing_task = session.scalar(select(TaskCandidateRow).where(
                    TaskCandidateRow.owner_id == owner_id,
                    TaskCandidateRow.idempotency_key == task_key,
                ))
                task_values = {
                    "knowledge_id": knowledge.id,
                    "title": (str(ai_summary["action_title"]) if ai_action else
                              title if source_kind == SourceKind.GOOGLE_CLASSROOM else f"Review: {title}")[:256],
                    "description": (summary_text + f"\n\nSource: {url}")[:4000],
                    "priority": _deadline_priority(due_date),
                    "due_date": due_date,
                    "evidence": {"source_id": str(source.id), "raw_record_id": raw.id,
                                 "url": url, "source_kind": source_kind.value,
                                 "excerpt": summary_text[:512]},
                    "updated_at": now,
                }
                if existing_task is None:
                    session.add(TaskCandidateRow(
                        owner_id=owner_id, idempotency_key=task_key,
                        status="open", review_state="needs_review", **task_values,
                        source_timezone="Asia/Kolkata",
                        created_at=now,
                    ))
                    counts["tasks"] += 1
                elif existing_task.review_state == "needs_review":
                    for field, value in task_values.items():
                        setattr(existing_task, field, value)

            notice_label = "College notice" if source_kind == SourceKind.COLLEGE_NOTICES else "Classroom update"
            action = "Review the deadline and required steps" if has_action else "Open and review the original update"
            recommendation_key = _digest("academic-notification-v1", str(owner_id), str(source.id), external_id)
            existing_recommendation = session.scalar(select(RecommendationRow).where(
                RecommendationRow.owner_id == owner_id,
                RecommendationRow.idempotency_key == recommendation_key,
            ))
            notification_values = {
                "knowledge_id": knowledge.id,
                "title": f"{notice_label}: {title}"[:256],
                "description": summary_text[:4000],
                "reason": (f"New item from {source_name}." +
                           (" A due date was found; verify it against the source." if due_date else
                            " No reliable due date was detected; check the original item.")),
                "priority": _deadline_priority(due_date),
                "suggested_action": action,
                "evidence": {"source_id": str(source.id), "raw_record_id": raw.id,
                             "url": url, "subject": title, "source_kind": source_kind.value,
                             "content_extracted": bool(item.get("content_extracted", True)),
                             "course": _short(item.get("course") or item.get("category") or item.get("kind"), 200),
                             "notice_number": _short(item.get("notice_number"), 80),
                             "published_at": item.get("published_at").isoformat() if isinstance(item.get("published_at"), datetime) else None,
                             "due_date": due_date.isoformat() if isinstance(due_date, date) else None},
            }
            if existing_recommendation is None:
                session.add(RecommendationRow(
                    owner_id=owner_id, idempotency_key=recommendation_key,
                    confidence=None, status="pending", created_at=now,
                    **notification_values,
                ))
                counts["notifications"] += 1
            else:
                was_pending = existing_recommendation.status == "pending"
                for field, value in notification_values.items():
                    setattr(existing_recommendation, field, value)
                if not was_pending:
                    existing_recommendation.status = "pending"
                    counts["notifications"] += 1
            session.flush()
            counts["processed"] += 1
        except Exception:
            session.rollback()
            # Keep previously committed owner/source state safe; a later sync can retry.
            raise

    sync_state.status = "idle"
    sync_state.last_success_at = now
    sync_state.updated_at = now
    session.commit()
    return {
        **counts,
        "sourceId": str(source.id),
        "aiSummarized": len(ai_summaries),
        "aiStatus": ai_status,
    }

