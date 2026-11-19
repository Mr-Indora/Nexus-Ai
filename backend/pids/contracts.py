"""Validated, persistence-independent contracts for the PIDS domain."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import date, datetime, timezone
from enum import StrEnum
from typing import AsyncIterator, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class SourceKind(StrEnum):
    GMAIL = "gmail"
    GOOGLE_CALENDAR = "google_calendar"
    COLLEGE_NOTICES = "college_notices"
    GOOGLE_CLASSROOM = "google_classroom"
    LOCAL_FILE = "local_file"
    MANUAL = "manual"


class SourceStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    DISCONNECTED = "disconnected"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    NEEDS_REVIEW = "needs_review"
    USER_CONFIRMED = "user_confirmed"


class KnowledgeLifecycleState(StrEnum):
    CANDIDATE = "candidate"
    REVIEWED = "reviewed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class FeedbackAction(StrEnum):
    CONFIRM = "confirm"
    CORRECT = "correct"
    REJECT = "reject"
    DISMISS = "dismiss"
    SNOOZE = "snooze"


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def _timezone_name(value: str | None) -> str | None:
    if value is not None:
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("must be a valid IANA timezone") from exc
    return value


def _normalize_identity_part(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value.strip())
    if not normalized:
        raise ValueError("identity component must not be empty")
    return normalized


class Source(Contract):
    id: UUID
    owner_id: int = Field(gt=0, strict=True)
    kind: SourceKind
    display_name: str = Field(min_length=1, max_length=120)
    account_ref: str | None = Field(default=None, min_length=1, max_length=512)
    status: SourceStatus = SourceStatus.ACTIVE
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime) -> datetime:
        return _utc_datetime(value)

    @model_validator(mode="after")
    def updated_not_before_created(self) -> Source:
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        return self


class SourceReference(Contract):
    owner_id: int = Field(gt=0, strict=True)
    source_id: UUID
    raw_record_id: UUID
    evidence_location: str | None = Field(default=None, min_length=1, max_length=512)


class EvidenceReference(Contract):
    id: UUID
    owner_id: int = Field(gt=0, strict=True)
    knowledge_id: UUID
    source_id: UUID
    raw_record_id: UUID
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_type: str = Field(min_length=1, max_length=32)
    offset_basis: str | None = Field(default=None, max_length=64)
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, ge=0)
    field_path: str | None = Field(default=None, max_length=512)
    extraction_method: str = Field(min_length=1, max_length=64)
    extraction_version: str = Field(min_length=1, max_length=128)
    processed_at: datetime
    transformation: dict[str, JsonValue] = Field(default_factory=dict)
    excerpt: str | None = Field(default=None, max_length=512)
    idempotency_key: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("payload_sha256", "idempotency_key", mode="before")
    @classmethod
    def lowercase_digest(cls, value: str) -> str:
        return value.lower() if isinstance(value, str) else value

    @field_validator("processed_at")
    @classmethod
    def processed_at_is_utc(cls, value: datetime) -> datetime:
        return _utc_datetime(value)

    @model_validator(mode="after")
    def evidence_coordinates_are_consistent(self) -> EvidenceReference:
        if (self.start_offset is None) != (self.end_offset is None):
            raise ValueError("evidence offsets must be supplied together")
        if self.start_offset is not None:
            if self.end_offset <= self.start_offset or not self.offset_basis:
                raise ValueError("text offsets require a positive range and offset basis")
        if self.evidence_type == "text" and self.start_offset is None:
            raise ValueError("text evidence requires offsets")
        if self.evidence_type == "structured" and not self.field_path:
            raise ValueError("structured evidence requires a field path")
        return self


class KnowledgeReviewEvent(Contract):
    id: UUID
    owner_id: int = Field(gt=0, strict=True)
    knowledge_id: UUID
    request_id: str = Field(min_length=1, max_length=128)
    from_state: KnowledgeLifecycleState
    to_state: KnowledgeLifecycleState
    reviewer_id: int = Field(gt=0, strict=True)
    decision: str = Field(min_length=1, max_length=64)
    rejection_reason: str | None = Field(default=None, max_length=1024)
    superseded_by_id: UUID | None = None
    version_before: int = Field(ge=1)
    version_after: int = Field(ge=2)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def timestamp_is_utc(cls, value: datetime) -> datetime:
        return _utc_datetime(value)

    @model_validator(mode="after")
    def validate_version_step(self) -> KnowledgeReviewEvent:
        if self.version_after != self.version_before + 1:
            raise ValueError("review event version must advance exactly once")
        return self


class KnowledgeConflict(Contract):
    id: UUID
    owner_id: int = Field(gt=0, strict=True)
    existing_id: UUID
    candidate_id: UUID
    conflict_type: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1024)
    status: str = Field(pattern=r"^(open|resolved)$")
    created_at: datetime
    resolved_at: datetime | None = None

    @field_validator("created_at", "resolved_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        return _utc_datetime(value) if value is not None else None


class RawRecord(Contract):
    id: UUID
    owner_id: int = Field(gt=0, strict=True)
    source_id: UUID
    external_id: str = Field(min_length=1, max_length=512)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_ref: str | None = Field(default=None, min_length=1, max_length=2048)
    payload_size_bytes: int | None = Field(default=None, ge=0)
    content_type: str | None = Field(default=None, min_length=1, max_length=128)
    character_encoding: str | None = Field(default=None, min_length=1, max_length=80)
    retention_expires_at: datetime | None = None
    source_timezone: str | None = None
    occurred_at: datetime | None = None
    ingested_at: datetime
    source_version: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("content_sha256", mode="before")
    @classmethod
    def lowercase_hash(cls, value: str) -> str:
        return value.lower() if isinstance(value, str) else value

    @field_validator("source_timezone")
    @classmethod
    def valid_source_timezone(cls, value: str | None) -> str | None:
        return _timezone_name(value)

    @field_validator("occurred_at", "ingested_at", "retention_expires_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        return _utc_datetime(value) if value is not None else None

    @field_validator("external_id")
    @classmethod
    def normalized_external_id(cls, value: str) -> str:
        return _normalize_identity_part(value)

    @field_validator("content_ref")
    @classmethod
    def safe_content_reference(cls, value: str | None) -> str | None:
        if value is not None and (
            value.startswith(("/", "\\"))
            or "\\" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
        ):
            raise ValueError("content_ref must be a relative vault reference")
        return value


class KnowledgeRecord(Contract):
    id: UUID
    owner_id: int = Field(gt=0, strict=True)
    record_type: str = Field(min_length=1, max_length=64)
    data: dict[str, JsonValue]
    source_refs: tuple[SourceReference, ...] = Field(min_length=1)
    processing_version: str = Field(min_length=1, max_length=128)
    verification: VerificationStatus = VerificationStatus.UNVERIFIED
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    created_at: datetime
    lifecycle_status: KnowledgeLifecycleState = KnowledgeLifecycleState.CANDIDATE
    updated_at: datetime | None = None
    reviewed_at: datetime | None = None
    reviewer_id: int | None = Field(default=None, gt=0, strict=True)
    review_decision: str | None = Field(default=None, max_length=64)
    rejection_reason: str | None = Field(default=None, max_length=1024)
    superseded_by_id: UUID | None = None
    version: int = Field(default=1, ge=1)

    @field_validator("created_at", "updated_at", "reviewed_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        return _utc_datetime(value) if value is not None else None

    @model_validator(mode="after")
    def lifecycle_fields_are_consistent(self) -> KnowledgeRecord:
        if self.updated_at is None:
            object.__setattr__(self, "updated_at", self.created_at)
        if self.lifecycle_status in {
            KnowledgeLifecycleState.REVIEWED,
            KnowledgeLifecycleState.CONFIRMED,
            KnowledgeLifecycleState.REJECTED,
            KnowledgeLifecycleState.SUPERSEDED,
        } and (self.reviewed_at is None or self.reviewer_id is None):
            raise ValueError("reviewed lifecycle states require reviewer and timestamp")
        if self.lifecycle_status == KnowledgeLifecycleState.REJECTED and not self.rejection_reason:
            raise ValueError("rejected knowledge requires a reason")
        if self.lifecycle_status == KnowledgeLifecycleState.SUPERSEDED and self.superseded_by_id is None:
            raise ValueError("superseded knowledge requires a successor")
        return self

    @model_validator(mode="after")
    def provenance_belongs_to_owner(self) -> KnowledgeRecord:
        if any(ref.owner_id != self.owner_id for ref in self.source_refs):
            raise ValueError("all source references must belong to owner_id")
        if len({ref.raw_record_id for ref in self.source_refs}) != len(self.source_refs):
            raise ValueError("source references must not repeat a raw_record_id")
        return self


class Entity(Contract):
    id: UUID
    owner_id: int = Field(gt=0, strict=True)
    entity_type: str = Field(min_length=1, max_length=64)
    canonical_name: str = Field(min_length=1, max_length=512)
    aliases: tuple[str, ...] = ()
    evidence: tuple[SourceReference, ...] = Field(min_length=1)
    created_at: datetime

    @field_validator("aliases")
    @classmethod
    def aliases_are_unique_and_nonempty(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(_normalize_identity_part(value) for value in values)
        if len({value.casefold() for value in normalized}) != len(normalized):
            raise ValueError("aliases must be unique")
        return normalized

    @field_validator("created_at")
    @classmethod
    def timestamp_is_utc(cls, value: datetime) -> datetime:
        return _utc_datetime(value)

    @model_validator(mode="after")
    def evidence_belongs_to_owner(self) -> Entity:
        if any(ref.owner_id != self.owner_id for ref in self.evidence):
            raise ValueError("all evidence must belong to owner_id")
        return self


class Relationship(Contract):
    id: UUID
    owner_id: int = Field(gt=0, strict=True)
    subject_id: UUID
    object_id: UUID
    relation_type: str = Field(min_length=1, max_length=64)
    evidence: tuple[SourceReference, ...] = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def timestamp_is_utc(cls, value: datetime) -> datetime:
        return _utc_datetime(value)

    @model_validator(mode="after")
    def validate_edge(self) -> Relationship:
        if self.subject_id == self.object_id:
            raise ValueError("relationship endpoints must differ")
        if any(ref.owner_id != self.owner_id for ref in self.evidence):
            raise ValueError("all evidence must belong to owner_id")
        return self


class Deadline(Contract):
    id: UUID
    owner_id: int = Field(gt=0, strict=True)
    record_id: UUID
    due_at: datetime | None = None
    due_date: date | None = None
    source_timezone: str | None = None
    evidence: tuple[SourceReference, ...] = Field(min_length=1)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    created_at: datetime

    @field_validator("due_date", mode="before")
    @classmethod
    def date_only_is_not_datetime(cls, value: date | None) -> date | None:
        if isinstance(value, datetime):
            raise ValueError("due_date must be date-only; use due_at for timestamps")
        return value

    @field_validator("source_timezone")
    @classmethod
    def valid_source_timezone(cls, value: str | None) -> str | None:
        return _timezone_name(value)

    @field_validator("due_at", "created_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        return _utc_datetime(value) if value is not None else None

    @model_validator(mode="after")
    def validate_deadline(self) -> Deadline:
        if (self.due_at is None) == (self.due_date is None):
            raise ValueError("exactly one of due_at or due_date is required")
        if any(ref.owner_id != self.owner_id for ref in self.evidence):
            raise ValueError("all evidence must belong to owner_id")
        return self


class Feedback(Contract):
    id: UUID
    owner_id: int = Field(gt=0, strict=True)
    target_type: str = Field(min_length=1, max_length=64)
    target_id: str = Field(min_length=1, max_length=128)
    action: FeedbackAction
    value: JsonValue | None = None
    snoozed_until: datetime | None = None
    occurred_at: datetime

    @field_validator("target_id")
    @classmethod
    def normalized_target_id(cls, value: str) -> str:
        return _normalize_identity_part(value)

    @field_validator("snoozed_until", "occurred_at")
    @classmethod
    def timestamps_are_utc(cls, value: datetime | None) -> datetime | None:
        return _utc_datetime(value) if value is not None else None

    @model_validator(mode="after")
    def action_has_matching_value(self) -> Feedback:
        has_value = "value" in self.model_fields_set
        if self.action == FeedbackAction.CORRECT and not has_value:
            raise ValueError("correct feedback requires a value")
        if self.action != FeedbackAction.CORRECT and has_value:
            raise ValueError("value is only valid for correct feedback")
        if self.action == FeedbackAction.SNOOZE and self.snoozed_until is None:
            raise ValueError("snooze feedback requires snoozed_until")
        if self.action != FeedbackAction.SNOOZE and self.snoozed_until is not None:
            raise ValueError("snoozed_until is only valid for snooze feedback")
        return self


class SourceConnector(Protocol):
    """Fetch source snapshots without binding the domain to a DB or provider."""

    kind: SourceKind

    def fetch(
        self, source: Source, *, cursor: str | None = None
    ) -> AsyncIterator[RawRecord]: ...


def idempotency_key(owner_id: int, source_id: UUID, external_id: str) -> str:
    """Stable exact-identity key; content similarity is deliberately excluded."""
    if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id <= 0:
        raise ValueError("owner_id must be a positive integer")
    identity = [
        "pids.raw-record.v1",
        owner_id,
        str(source_id),
        _normalize_identity_part(external_id),
    ]
    encoded = json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def content_sha256(content: bytes) -> str:
    """Return a content digest for comparison; it is not an ingestion identity."""
    if not isinstance(content, bytes):
        raise TypeError("content must be bytes")
    return hashlib.sha256(content).hexdigest()


def same_source_item(left: RawRecord, right: RawRecord) -> bool:
    """Detect exact provider identity, not fuzzy/content-based duplicates."""
    return (
        left.owner_id == right.owner_id
        and left.source_id == right.source_id
        and left.external_id == right.external_id
    )


def same_content_candidate(left: RawRecord, right: RawRecord) -> bool:
    """Flag equal content hashes for review; never merge records automatically."""
    return left.owner_id == right.owner_id and left.content_sha256 == right.content_sha256
