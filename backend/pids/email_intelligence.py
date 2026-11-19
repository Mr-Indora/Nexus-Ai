"""Deterministic, evidence-linked email and thread intelligence for PIDS."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timezone
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from pydantic import Field, JsonValue
from sqlalchemy.orm import Session

from pids.contracts import (
    EvidenceReference,
    Contract,
    KnowledgeRecord,
    Source,
    SourceReference,
    VerificationStatus,
)
from pids.normalization import IncomingRecord, normalize_text
from pids.persistence.database import begin_nested_transaction
from pids.persistence.repositories import PidsRepositories
from pids.pipeline import IntelligencePipeline, PipelineResult
from pids.understanding import understand
from pids.vault import RawDataVault

EMAIL_RULE_VERSION = "pids-email-rules-v3"
THREAD_RULE_VERSION = "pids-email-thread-rules-v3"

_CATEGORY_RULES = {
    "career": ("recruit", "interview", "job", "internship", "application", "offer"),
    "education": ("assignment", "course", "university", "college", "professor", "exam"),
    "finance": ("invoice", "payment", "bill", "amount due", "transaction"),
    "travel": ("flight", "hotel", "itinerary", "boarding", "reservation"),
    "security": ("password reset", "security alert", "sign-in alert", "verification code"),
    "marketing": ("unsubscribe", "newsletter", "discount", "sale", "promotion", "webinar recording"),
    "notification": ("automated message", "do not reply", "no-reply", "noreply"),
    "work": ("project", "client", "deliverable", "team", "work"),
}
_DIRECT_REPLY = re.compile(
    r"\b(?:please\s+)?(?:reply|respond|confirm|let me know)\b"
    r"|\b(?:reply|respond)\s+(?:to this email|by|with)\b"
    r"|\bconfirm\s+(?:your|whether|if|that|when|availability|attendance)\b",
    re.IGNORECASE,
)
_PROMO = re.compile(r"\b(?:sale|discount|promotion|newsletter|limited offer|sponsored offer)\b", re.I)
_AUTOMATED_SENDER = re.compile(r"(?:no[-_]?reply|do[-_]?not[-_]?reply|notifications?)@", re.I)
_RECRUITER = re.compile(r"\b(?:recruiter|recruiting|talent acquisition|interview|job offer)\b", re.I)
_FOLLOWUP = re.compile(r"\b(?:follow[- ]?up|following up|checking in|any update)\b", re.I)
_URGENT = re.compile(r"\b(?:asap|immediately|urgent|final notice)\b", re.I)
_QUOTED_BOUNDARY = re.compile(
    r"^(?:on .{1,160}wrote:|-{2,}\s*original message\s*-{2,}|from:\s|begin forwarded message:)",
    re.IGNORECASE,
)


class EmailMessageInput(IncomingRecord):
    external_id: str = Field(min_length=1, max_length=512)
    occurred_at: datetime
    sender: str = Field(min_length=1, max_length=512)
    subject: str = Field(default="", max_length=1024)
    thread_id: str | None = Field(default=None, max_length=128)
    direction: Literal["incoming", "outgoing"] = "incoming"

    @property
    def message_text(self) -> str:
        return "\n".join(part for part in (self.subject.strip(), self.decoded_text.strip()) if part)


class EmailActionCandidate(Contract):
    title: str = Field(min_length=1, max_length=256)
    description: str = Field(default="", max_length=1024)
    evidence: str = Field(min_length=1, max_length=1024)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    due_date: str | None = None
    deadline_status: str = "not_identified"
    explicit_commitment: bool = False


class EmailIntelligenceResult(Contract):
    categories: tuple[str, ...] = ()
    importance: Literal["critical", "high", "normal", "low", "informational"]
    intent: str
    reply_state: Literal[
        "reply_required", "reply_recommended", "reply_optional",
        "no_reply_required", "waiting_for_other_party",
    ]
    reply_reason: str | None = None
    sender_class: str
    summary: str
    what_happened: str
    why_it_matters: str
    what_sender_expects: str
    important_dates: tuple[dict[str, JsonValue], ...] = ()
    actions: tuple[EmailActionCandidate, ...] = ()
    outstanding_questions: tuple[str, ...] = ()
    follow_up_candidate: bool = False
    follow_up_reason: str | None = None
    next_step: str
    analysis_version: str = EMAIL_RULE_VERSION
    method: Literal["deterministic_rules"] = "deterministic_rules"


class EmailThreadIntelligenceResult(Contract):
    thread_id: str
    message_count: int = Field(ge=1)
    latest_message_at: datetime
    summary: str
    latest_status: str
    reply_state: Literal[
        "reply_required", "reply_recommended", "reply_optional",
        "no_reply_required", "waiting_for_other_party",
    ]
    deadlines: tuple[str, ...] = ()
    superseded_deadlines: tuple[str, ...] = ()
    commitments: tuple[str, ...] = ()
    resolved_actions: tuple[str, ...] = ()
    pending_actions: tuple[str, ...] = ()
    contradictions: tuple[str, ...] = ()
    source_refs: tuple[SourceReference, ...]
    analysis_version: str = THREAD_RULE_VERSION


def _intent(text: str, actions: tuple[EmailActionCandidate, ...]) -> str:
    lowered = text.casefold()
    rules = (
        ("cancellation", ("cancel", "canceled", "cancelled")),
        ("rescheduling", ("reschedule", "move the meeting", "works instead")),
        ("completion_confirmation", ("completed", "has been completed", "finished")),
        ("rejection", ("we regret", "not selected", "application rejected")),
        ("opportunity", ("offer", "invited to interview", "opportunity")),
        ("payment_request", ("invoice", "payment due", "please pay")),
        ("document_request", ("submit your", "provide your", "upload the")),
        ("approval_request", ("approve", "approval", "please authorize")),
        ("scheduling", ("meeting", "interview", "schedule", "availability")),
        ("follow_up", ("following up", "checking in", "any update")),
        ("action_request", ("please", "kindly", "need you to")),
        ("commitment", ("i will ", "we will ", "i'll ", "we'll ")),
    )
    for label, phrases in rules:
        if any(phrase in lowered for phrase in phrases):
            return label
    if actions:
        return "action_request"
    return "information_sharing"


def _sender_class(sender: str, body: str) -> str:
    lowered = f"{sender} {body}".casefold()
    if _AUTOMATED_SENDER.search(sender):
        return "automated_system"
    if _RECRUITER.search(lowered):
        return "recruiter"
    if any(token in lowered for token in ("university", "college", "professor", "campus")):
        return "education"
    if any(token in lowered for token in ("invoice", "bank", "payment", "account statement")):
        return "financial_institution_or_vendor"
    return "unknown"


def _strip_quoted(text: str) -> str:
    kept = []
    for line in text.splitlines():
        if line.lstrip().startswith(">") or _QUOTED_BOUNDARY.match(line.strip()):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def analyze_email(message: EmailMessageInput) -> EmailIntelligenceResult:
    body = _strip_quoted(message.decoded_text)
    text = normalize_text("\n".join(part for part in (message.subject.strip(), body) if part))
    understanding = understand(
        text,
        reference_time=message.occurred_at,
        source_timezone=message.source_timezone,
    )
    actions: list[EmailActionCandidate] = []
    for candidate in understanding.tasks:
        start, end = candidate.start, candidate.end
        evidence = text[start:end]
        due_candidates = [
            deadline for deadline in understanding.deadlines
            if deadline.start == candidate.start
            and deadline.fields.get("deadline_signal") is True
        ]
        due = due_candidates[0] if due_candidates else None
        title = str(candidate.fields.get("description", evidence)).strip()
        actions.append(
            EmailActionCandidate(
                title=title[:256] or "Review requested action",
                description=evidence[:1024],
                evidence=evidence[:1024],
                start=start,
                end=end,
                due_date=due.fields.get("due_date") if due else None,
                deadline_status=(
                    str(due.fields.get("resolution", "unresolved"))
                    if due else "not_identified"
                ),
                explicit_commitment=bool(re.search(r"\b(?:i|we)\s+will\b", evidence, re.I)),
            )
        )

    # Course announcement emails often end with the generic line “Please Check.”
    # That line alone is not a task. If the same message clearly announces a
    # released graded assignment and gives an explicit due date, create one
    # reviewable, useful suggestion tied to the announcement evidence.
    assignment_release = re.search(
        r"\b(?:graded\s+)?assignment\b[^.!?\n]{0,100}\b(?:released|available|posted)\b"
        r"|\b(?:released|available|posted)\b[^.!?\n]{0,100}\b(?:graded\s+)?assignment\b",
        text,
        re.IGNORECASE,
    )
    explicit_assignment_deadline = next((item for item in understanding.deadlines if (
        item.fields.get("deadline_signal") is True
        and item.fields.get("due_date")
        and str(item.fields.get("resolution", "")).startswith("resolved")
    )), None)
    if not actions and assignment_release and explicit_assignment_deadline:
        week = re.search(r"\bweek\s+(\d{1,2})\b", text, re.IGNORECASE)
        title = f"Submit Week {week.group(1)} graded assignment" if week else "Submit graded assignment"
        evidence = assignment_release.group().strip()
        actions.append(EmailActionCandidate(
            title=title,
            description=f"The course announcement says the graded assignment is available. {explicit_assignment_deadline.evidence}",
            evidence=evidence,
            start=assignment_release.start(),
            end=assignment_release.end(),
            due_date=str(explicit_assignment_deadline.fields["due_date"]),
            deadline_status=str(explicit_assignment_deadline.fields["resolution"]),
        ))

    lowered = text.casefold()
    is_promo = bool(_PROMO.search(lowered)) and not bool(_DIRECT_REPLY.search(body))
    categories = tuple(
        category for category, terms in _CATEGORY_RULES.items()
        if any(term in lowered for term in terms)
    )
    if not categories:
        categories = ("personal",) if "@" in message.sender else ("informational",)
    if is_promo and "marketing" not in categories:
        categories = (*categories, "marketing")

    explicitly_requests_reply = bool(_DIRECT_REPLY.search(body))
    automated_sender = bool(_AUTOMATED_SENDER.search(message.sender))
    # Automated/bulk senders routinely say “please check”, “submit”, or “apply”.
    # Those may create a task candidate, but they do not mean the user must reply.
    direct_reply = explicitly_requests_reply and not automated_sender
    question_sentences = tuple(
        sentence.strip()[:300]
        for sentence in re.split(r"(?<=[.!?])\s+|\n", body)
        if "?" in sentence
    )[:10]
    if message.direction == "outgoing":
        reply_state = "waiting_for_other_party"
        reply_reason = "This is an outgoing message; a response from the other party is not yet established."
    elif automated_sender:
        reply_state = "no_reply_required"
        reply_reason = "This is an automated sender; review any listed action, but no email reply is expected."
    elif direct_reply:
        reply_state = "reply_required"
        reply_reason = "The current message explicitly asks for a response or confirmation."
    elif question_sentences:
        reply_state = "reply_recommended"
        reply_reason = "The current message contains an unanswered question."
    elif is_promo:
        reply_state = "no_reply_required"
        reply_reason = "The message appears promotional or automated and contains no direct request."
    else:
        reply_state = "reply_optional"
        reply_reason = None

    dates = tuple(
        {
            "expression": item.fields.get("expression"),
            "date": item.fields.get("due_date"),
            "resolution": item.fields.get("resolution"),
            "deadline": item.fields.get("deadline_signal") is True,
            "evidence": item.evidence,
            "start": item.start,
            "end": item.end,
        }
        for item in understanding.deadlines
    )
    followup = bool(_FOLLOWUP.search(body)) and message.direction == "incoming"
    intent = _intent(text, tuple(actions))
    if is_promo:
        importance = "low"
    elif any(
        item["deadline"] and item["resolution"].startswith("resolved")
        and item["date"] and (date.fromisoformat(item["date"]) - (
            message.occurred_at.astimezone(ZoneInfo(message.source_timezone)).date()
            if message.source_timezone else message.occurred_at.date()
        )).days <= 2
        for item in dates
    ):
        importance = "high" if direct_reply or actions else "normal"
    elif direct_reply and actions:
        importance = "high"
    elif actions:
        importance = "normal"
    elif _URGENT.search(body):
        importance = "normal"
    else:
        importance = "informational"

    clean_body = " ".join(body.split())
    summary = (clean_body or message.subject or "No substantive message content.")[:320]
    what_happened = message.subject.strip() or summary[:180]
    why = (
        f"Contains {', '.join(categories)} information and {len(actions)} explicit action candidate(s)."
        if actions else f"Classified as {', '.join(categories)}; no explicit action was extracted."
    )
    expects = (
        "A response or confirmation is explicitly requested."
        if reply_state == "reply_required"
        else "The message contains a question; a response may be appropriate."
        if reply_state == "reply_recommended"
        else "No response expectation was established from the available message."
    )
    next_step = (
        actions[0].title if actions else
        "Review the message and decide whether a response is needed."
        if reply_state in {"reply_required", "reply_recommended"}
        else "No action identified."
    )
    return EmailIntelligenceResult(
        categories=categories,
        importance=importance,
        intent=intent,
        reply_state=reply_state,
        reply_reason=reply_reason,
        sender_class=_sender_class(message.sender, body),
        summary=summary,
        what_happened=what_happened,
        why_it_matters=why,
        what_sender_expects=expects,
        important_dates=dates,
        actions=tuple(actions),
        outstanding_questions=question_sentences,
        follow_up_candidate=followup,
        follow_up_reason="The sender explicitly follows up; response ownership remains unconfirmed." if followup else None,
        next_step=next_step,
    )


def analyze_thread(
    thread_id: str, messages: tuple[tuple[EmailMessageInput, EmailIntelligenceResult, SourceReference], ...]
) -> EmailThreadIntelligenceResult:
    if not thread_id or not messages:
        raise ValueError("thread analysis requires a thread id and at least one message")
    ordered = tuple(sorted(messages, key=lambda item: item[0].occurred_at))
    latest_message, latest_analysis, _ = ordered[-1]
    owner_ids = {reference.owner_id for _, _, reference in ordered}
    source_ids = {reference.source_id for _, _, reference in ordered}
    if len(owner_ids) != 1 or len(source_ids) != 1:
        raise ValueError("thread evidence must share one owner and source")
    latest_state = "latest_message_received" if latest_message.direction == "incoming" else "user_replied"
    resolved: list[str] = []
    pending: list[str] = []
    for index, (_, result, _) in enumerate(ordered):
        for action in result.actions:
            action_words = {
                word.casefold()
                for word in re.findall(r"[A-Za-z]{4,}", action.evidence)
                if word.casefold() not in {"please", "submit", "send", "complete", "review", "confirm"}
            }
            completed = any(
                later.direction == "outgoing"
                and re.search(r"\b(?:completed|finished|done|submitted|sent|attached)\b", later.decoded_text, re.I)
                and bool(action_words.intersection(
                    word.casefold() for word in re.findall(r"[A-Za-z]{4,}", later.decoded_text)
                ))
                for later, _, _ in ordered[index + 1:]
            )
            (resolved if completed else pending).append(action.title)
    active_dates: list[str] = []
    superseded_dates: list[str] = []
    for _, result, _ in ordered:
        message_dates = [
            item["date"]
            for item in sorted(result.important_dates, key=lambda item: int(item.get("start", 0)))
            if item.get("date") and (
                item.get("deadline")
                or result.intent == "rescheduling"
                or (
                    result.intent == "scheduling"
                    and any(category in {"career", "education", "travel"} for category in result.categories)
                )
            )
        ]
        message_dates = list(dict.fromkeys(str(item) for item in message_dates))
        if not message_dates:
            continue
        if result.intent == "rescheduling":
            superseded_dates.extend(active_dates)
            superseded_dates.extend(message_dates[:-1])
            active_dates = [message_dates[-1]]
        else:
            active_dates.extend(message_dates)
            active_dates = list(dict.fromkeys(active_dates))
    deadlines = tuple(active_dates)
    superseded_deadlines = tuple(dict.fromkeys(
        item for item in superseded_dates if item not in active_dates
    ))
    contradictions = ()
    if len(set(deadlines)) > 1:
        contradictions = ("Thread contains multiple explicit deadline dates; preserve both for review.",)
    commitments = tuple(
        action.title
        for _, result, _ in ordered
        for action in result.actions
        if action.explicit_commitment
    )
    return EmailThreadIntelligenceResult(
        thread_id=thread_id,
        message_count=len(ordered),
        latest_message_at=latest_message.occurred_at or datetime.now(timezone.utc),
        summary=latest_analysis.summary,
        latest_status=latest_state,
        reply_state=latest_analysis.reply_state,
        deadlines=deadlines,
        superseded_deadlines=superseded_deadlines,
        commitments=commitments,
        resolved_actions=tuple(resolved),
        pending_actions=tuple(pending),
        contradictions=contradictions,
        source_refs=tuple(item[2] for item in ordered),
    )


class EmailIntelligenceService:
    """Persist deterministic email outputs as provenance-linked PIDS candidates."""

    def __init__(self, session: Session, *, vault: RawDataVault | None = None):
        self.session = session
        self.vault = vault or RawDataVault()
        self.repositories = PidsRepositories(session)
        self.pipeline = IntelligencePipeline(session, vault=self.vault)

    def process_email(
        self, message: EmailMessageInput, source: Source
    ) -> tuple[EmailIntelligenceResult, UUID, UUID]:
        if message.source_id != source.id or message.owner_id != source.owner_id:
            raise ValueError("email owner/source does not match registered source")
        text = message.message_text
        prepared = IncomingRecord(
            owner_id=message.owner_id,
            source_id=message.source_id,
            external_id=message.external_id,
            text=text,
            content_type="text/plain",
            retention_expires_at=message.retention_expires_at,
            record_type="email",
            occurred_at=message.occurred_at,
            source_timezone=message.source_timezone,
            source_version=message.source_version,
            metadata={
                **message.metadata,
                "sender": message.sender,
                "subject": message.subject,
                "thread_id": message.thread_id,
                "direction": message.direction,
            },
        )
        result = analyze_email(message)
        with begin_nested_transaction(self.session):
            pipeline_result: PipelineResult = self.pipeline.process(
                message.owner_id, source, (prepared,)
            )
            item = pipeline_result.records[0]
            if item.status == "failed" or item.raw_record_id is None:
                raise ValueError(f"email processing failed: {item.error_code or 'unknown'}")
            raw = self.repositories.raw_records.get(message.owner_id, item.raw_record_id)
            source_ref = SourceReference(
                owner_id=message.owner_id,
                source_id=source.id,
                raw_record_id=raw.id,
                evidence_location="email_message",
            )
            knowledge = KnowledgeRecord(
                id=uuid5(
                    NAMESPACE_URL,
                    f"pids-email-v1:{message.owner_id}:{raw.id}:{EMAIL_RULE_VERSION}",
                ),
                owner_id=message.owner_id,
                record_type="email_intelligence",
                data=result.model_dump(mode="json"),
                source_refs=(source_ref,),
                processing_version=EMAIL_RULE_VERSION,
                verification=VerificationStatus.NEEDS_REVIEW,
                created_at=message.occurred_at or datetime.now(timezone.utc),
            )
            persisted = self.repositories.knowledge_records.create_idempotent(knowledge)
            self._persist_evidence(persisted, raw.content_sha256, raw.ingested_at, text, source_ref)
        return result, persisted.id, raw.id

    def _persist_evidence(
        self,
        knowledge: KnowledgeRecord,
        digest: str,
        processed_at: datetime,
        text: str,
        source_ref: SourceReference,
    ) -> None:
        normalized = normalize_text(text)
        if not normalized:
            return
        key = hashlib.sha256(
            f"{knowledge.owner_id}:{knowledge.id}:{digest}:email-message".encode("utf-8")
        ).hexdigest()
        evidence = EvidenceReference(
            id=uuid5(NAMESPACE_URL, f"pids-email-evidence:{key}"),
            owner_id=knowledge.owner_id,
            knowledge_id=knowledge.id,
            source_id=source_ref.source_id,
            raw_record_id=source_ref.raw_record_id,
            payload_sha256=digest,
            evidence_type="text",
            offset_basis="normalized_text_unicode_codepoints",
            start_offset=0,
            end_offset=len(normalized),
            extraction_method="deterministic_email_rules",
            extraction_version=EMAIL_RULE_VERSION,
            processed_at=processed_at,
            transformation={
                "normalized_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            },
            excerpt=normalized[:512],
            idempotency_key=key,
        )
        self.repositories.evidence.create_idempotent(evidence, self.vault)

    def process_thread(
        self, source: Source, messages: tuple[EmailMessageInput, ...]
    ) -> tuple[EmailThreadIntelligenceResult, UUID]:
        if not messages or not messages[0].thread_id:
            raise ValueError("all thread messages must include a thread_id")
        thread_id = messages[0].thread_id
        if any(item.thread_id != thread_id for item in messages):
            raise ValueError("thread messages must share the same thread_id")
        with begin_nested_transaction(self.session):
            analyzed = []
            for message in sorted(
                messages,
                key=lambda item: item.occurred_at or datetime.min.replace(tzinfo=timezone.utc),
            ):
                result, _, raw_id = self.process_email(message, source)
                analyzed.append(
                    (
                        message,
                        result,
                        SourceReference(
                            owner_id=message.owner_id,
                            source_id=source.id,
                            raw_record_id=raw_id,
                            evidence_location="email_thread_message",
                        ),
                    )
                )
            return self.process_thread_analyses(source, tuple(analyzed))

    def process_thread_analyses(
        self,
        source: Source,
        messages: tuple[tuple[EmailMessageInput, EmailIntelligenceResult, SourceReference], ...],
    ) -> tuple[EmailThreadIntelligenceResult, UUID]:
        """Rebuild thread state from persisted message analyses and source evidence."""
        if not messages or not messages[0][0].thread_id:
            raise ValueError("all thread messages must include a thread_id")
        thread_id = messages[0][0].thread_id
        if any(item[0].thread_id != thread_id for item in messages):
            raise ValueError("thread messages must share the same thread_id")
        if any(
            item[0].owner_id != source.owner_id or item[0].source_id != source.id
            for item in messages
        ):
            raise ValueError("thread messages must belong to the registered owner and source")
        thread_result = analyze_thread(thread_id, messages)
        raw_identity = ":".join(sorted(
            str(reference.raw_record_id) for _, _, reference in messages
        ))
        knowledge = KnowledgeRecord(
            id=uuid5(
                NAMESPACE_URL,
                f"pids-email-thread-v1:{source.owner_id}:{source.id}:{thread_id}:{THREAD_RULE_VERSION}:{raw_identity}",
            ),
            owner_id=source.owner_id,
            record_type="email_thread_intelligence",
            data=thread_result.model_dump(mode="json", exclude={"source_refs"}),
            source_refs=tuple(sorted(
                thread_result.source_refs,
                key=lambda reference: (str(reference.source_id), str(reference.raw_record_id)),
            )),
            processing_version=THREAD_RULE_VERSION,
            verification=VerificationStatus.NEEDS_REVIEW,
            created_at=thread_result.latest_message_at,
        )
        persisted = self.repositories.knowledge_records.create_idempotent(knowledge)
        return thread_result, persisted.id
