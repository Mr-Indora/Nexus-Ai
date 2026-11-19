"""PIDS-only SQLAlchemy metadata and relational mappings."""

from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    Boolean,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """Store UTC instants portably and return timezone-aware UTC values."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, _dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("PIDS timestamps must be timezone-aware")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, _dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def _id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SourceRow(Base):
    __tablename__ = "pids_sources"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_pids_sources_owner_id"),
        Index("ix_pids_sources_owner_status", "owner_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    account_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, onupdate=_now)


class RawRecordRow(Base):
    __tablename__ = "pids_raw_records"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_pids_raw_owner_id"),
        UniqueConstraint("owner_id", "source_id", "id", name="uq_pids_raw_owner_source_id"),
        UniqueConstraint(
            "owner_id", "source_id", "external_id", "source_version",
            name="uq_pids_raw_source_version",
        ),
        ForeignKeyConstraint(
            ["owner_id", "source_id"],
            ["pids_sources.owner_id", "pids_sources.id"],
            name="fk_pids_raw_source_owner",
            ondelete="RESTRICT",
        ),
        Index("ix_pids_raw_owner_hash", "owner_id", "content_sha256"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_ref: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    payload_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    character_encoding: Mapped[str | None] = mapped_column(String(80), nullable=True)
    retention_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    source_timezone: Mapped[str | None] = mapped_column(String(128), nullable=True)
    occurred_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    source_version: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class KnowledgeRecordRow(Base):
    __tablename__ = "pids_knowledge_records"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_pids_knowledge_owner_id"),
        UniqueConstraint(
            "owner_id", "idempotency_key", name="uq_pids_knowledge_idempotency"
        ),
        Index("ix_pids_knowledge_owner_type", "owner_id", "record_type"),
        Index("ix_pids_knowledge_owner_lifecycle", "owner_id", "lifecycle_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    record_type: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    processing_version: Mapped[str] = mapped_column(String(128), nullable=False)
    verification: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    lifecycle_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="candidate", server_default="candidate"
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    reviewer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    review_decision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    superseded_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, onupdate=_now)


class EvidenceReferenceRow(Base):
    __tablename__ = "pids_evidence_references"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "knowledge_id", "idempotency_key",
            name="uq_pids_evidence_idempotency",
        ),
        ForeignKeyConstraint(
            ["owner_id", "knowledge_id"],
            ["pids_knowledge_records.owner_id", "pids_knowledge_records.id"],
            name="fk_pids_evidence_knowledge_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "source_id", "raw_record_id"],
            ["pids_raw_records.owner_id", "pids_raw_records.source_id", "pids_raw_records.id"],
            name="fk_pids_evidence_raw_owner",
            ondelete="RESTRICT",
        ),
        Index("ix_pids_evidence_raw", "owner_id", "source_id", "raw_record_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    knowledge_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    raw_record_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    offset_basis: Mapped[str | None] = mapped_column(String(64), nullable=True)
    start_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_offset: Mapped[int | None] = mapped_column(Integer, nullable=True)
    field_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    extraction_method: Mapped[str] = mapped_column(String(64), nullable=False)
    extraction_version: Mapped[str] = mapped_column(String(128), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    transformation: Mapped[dict] = mapped_column(JSON, nullable=False)
    excerpt: Mapped[str | None] = mapped_column(String(512), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)


class KnowledgeReviewEventRow(Base):
    __tablename__ = "pids_knowledge_review_events"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "knowledge_id", "request_id",
            name="uq_pids_review_request",
        ),
        ForeignKeyConstraint(
            ["owner_id", "knowledge_id"],
            ["pids_knowledge_records.owner_id", "pids_knowledge_records.id"],
            name="fk_pids_review_knowledge_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "superseded_by_id"],
            ["pids_knowledge_records.owner_id", "pids_knowledge_records.id"],
            name="fk_pids_review_successor_owner",
            ondelete="RESTRICT",
        ),
        Index("ix_pids_review_knowledge_version", "owner_id", "knowledge_id", "version_after"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    knowledge_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    from_state: Mapped[str] = mapped_column(String(16), nullable=False)
    to_state: Mapped[str] = mapped_column(String(16), nullable=False)
    reviewer_id: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(64), nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    superseded_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    version_before: Mapped[int] = mapped_column(Integer, nullable=False)
    version_after: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class KnowledgeConflictRow(Base):
    __tablename__ = "pids_knowledge_conflicts"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "existing_id", "candidate_id",
            name="uq_pids_knowledge_conflict_pair",
        ),
        ForeignKeyConstraint(
            ["owner_id", "existing_id"],
            ["pids_knowledge_records.owner_id", "pids_knowledge_records.id"],
            name="fk_pids_conflict_existing_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "candidate_id"],
            ["pids_knowledge_records.owner_id", "pids_knowledge_records.id"],
            name="fk_pids_conflict_candidate_owner",
            ondelete="RESTRICT",
        ),
        Index("ix_pids_conflicts_candidate", "owner_id", "candidate_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    existing_id: Mapped[str] = mapped_column(String(36), nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    conflict_type: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class KnowledgeSourceRow(Base):
    __tablename__ = "pids_knowledge_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "knowledge_id"],
            ["pids_knowledge_records.owner_id", "pids_knowledge_records.id"],
            name="fk_pids_knowledge_source_knowledge_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "source_id", "raw_record_id"],
            ["pids_raw_records.owner_id", "pids_raw_records.source_id", "pids_raw_records.id"],
            name="fk_pids_knowledge_source_raw_owner",
            ondelete="RESTRICT",
        ),
        Index("ix_pids_knowledge_sources_raw", "owner_id", "source_id", "raw_record_id"),
    )

    owner_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    knowledge_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    raw_record_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    evidence_location: Mapped[str | None] = mapped_column(String(512), nullable=True)


class EntityRow(Base):
    __tablename__ = "pids_entities"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_pids_entities_owner_id"),
        Index("ix_pids_entities_owner_type", "owner_id", "entity_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, onupdate=_now)


class EntityAliasRow(Base):
    __tablename__ = "pids_entity_aliases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "entity_id"],
            ["pids_entities.owner_id", "pids_entities.id"],
            name="fk_pids_entity_alias_owner",
            ondelete="CASCADE",
        ),
        Index("ix_pids_entity_aliases_owner_alias", "owner_id", "alias"),
    )

    owner_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    alias: Mapped[str] = mapped_column(String(512), primary_key=True)


class KnowledgeEntityRow(Base):
    __tablename__ = "pids_knowledge_entities"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "knowledge_id"],
            ["pids_knowledge_records.owner_id", "pids_knowledge_records.id"],
            name="fk_pids_knowledge_entity_knowledge_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "entity_id"],
            ["pids_entities.owner_id", "pids_entities.id"],
            name="fk_pids_knowledge_entity_entity_owner",
            ondelete="RESTRICT",
        ),
        Index("ix_pids_knowledge_entities_entity", "owner_id", "entity_id"),
    )

    owner_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    knowledge_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(36), primary_key=True)


class EntityEvidenceRow(Base):
    __tablename__ = "pids_entity_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "entity_id"],
            ["pids_entities.owner_id", "pids_entities.id"],
            name="fk_pids_entity_evidence_entity_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "source_id", "raw_record_id"],
            ["pids_raw_records.owner_id", "pids_raw_records.source_id", "pids_raw_records.id"],
            name="fk_pids_entity_evidence_raw_owner",
            ondelete="RESTRICT",
        ),
        Index("ix_pids_entity_evidence_raw", "owner_id", "source_id", "raw_record_id"),
    )

    owner_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    raw_record_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    evidence_location: Mapped[str | None] = mapped_column(String(512), nullable=True)


class RelationshipRow(Base):
    __tablename__ = "pids_relationships"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_pids_relationships_owner_id"),
        ForeignKeyConstraint(
            ["owner_id", "subject_id"],
            ["pids_entities.owner_id", "pids_entities.id"],
            name="fk_pids_relationship_subject_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_id", "object_id"],
            ["pids_entities.owner_id", "pids_entities.id"],
            name="fk_pids_relationship_object_owner",
            ondelete="RESTRICT",
        ),
        Index("ix_pids_relationships_subject", "owner_id", "subject_id"),
        Index("ix_pids_relationships_object", "owner_id", "object_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_id: Mapped[str] = mapped_column(String(36), nullable=False)
    object_id: Mapped[str] = mapped_column(String(36), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, onupdate=_now)


class RelationshipEvidenceRow(Base):
    __tablename__ = "pids_relationship_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "relationship_id"],
            ["pids_relationships.owner_id", "pids_relationships.id"],
            name="fk_pids_relationship_evidence_relationship_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "source_id", "raw_record_id"],
            ["pids_raw_records.owner_id", "pids_raw_records.source_id", "pids_raw_records.id"],
            name="fk_pids_relationship_evidence_raw_owner",
            ondelete="RESTRICT",
        ),
        Index("ix_pids_relationship_evidence_raw", "owner_id", "source_id", "raw_record_id"),
    )

    owner_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    relationship_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    raw_record_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    evidence_location: Mapped[str | None] = mapped_column(String(512), nullable=True)


class DeadlineRow(Base):
    __tablename__ = "pids_deadlines"
    __table_args__ = (
        UniqueConstraint("owner_id", "id", name="uq_pids_deadlines_owner_id"),
        ForeignKeyConstraint(
            ["owner_id", "record_id"],
            ["pids_knowledge_records.owner_id", "pids_knowledge_records.id"],
            name="fk_pids_deadline_knowledge_owner",
            ondelete="RESTRICT",
        ),
        Index("ix_pids_deadlines_owner_record", "owner_id", "record_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    record_id: Mapped[str] = mapped_column(String(36), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_timezone: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, onupdate=_now)


class DeadlineEvidenceRow(Base):
    __tablename__ = "pids_deadline_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "deadline_id"],
            ["pids_deadlines.owner_id", "pids_deadlines.id"],
            name="fk_pids_deadline_evidence_deadline_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "source_id", "raw_record_id"],
            ["pids_raw_records.owner_id", "pids_raw_records.source_id", "pids_raw_records.id"],
            name="fk_pids_deadline_evidence_raw_owner",
            ondelete="RESTRICT",
        ),
        Index("ix_pids_deadline_evidence_raw", "owner_id", "source_id", "raw_record_id"),
    )

    owner_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deadline_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    raw_record_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    evidence_location: Mapped[str | None] = mapped_column(String(512), nullable=True)


class SourceSyncStateRow(Base):
    __tablename__ = "pids_source_sync_state"
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "source_id"],
            ["pids_sources.owner_id", "pids_sources.id"],
            name="fk_pids_sync_state_source_owner",
            ondelete="CASCADE",
        ),
        Index("ix_pids_sync_state_due", "status", "next_sync_at"),
    )

    owner_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="idle")
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    next_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, onupdate=_now)


class NotificationPreferenceRow(Base):
    """Per-owner opt-in preferences for durable in-app reminders."""
    __tablename__ = "pids_notification_preferences"

    owner_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    daily_brief_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    daily_brief_time: Mapped[str] = mapped_column(String(5), nullable=False, default="06:00")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    deadline_reminders: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    high_priority_alerts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_now)


class NotificationDeliveryRow(Base):
    """Idempotent in-app notifications, kept separate from source records."""
    __tablename__ = "pids_notifications"
    __table_args__ = (
        UniqueConstraint("owner_id", "delivery_key", name="uq_pids_notification_delivery"),
        Index("ix_pids_notifications_owner_created", "owner_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    delivery_key: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_now)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class EmailMessageRow(Base):
    __tablename__ = "pids_email_messages"
    __table_args__ = (
        UniqueConstraint("owner_id", "source_id", "external_id", name="uq_pids_email_external"),
        ForeignKeyConstraint(
            ["owner_id", "source_id", "raw_record_id"],
            ["pids_raw_records.owner_id", "pids_raw_records.source_id", "pids_raw_records.id"],
            name="fk_pids_email_raw_owner",
            ondelete="RESTRICT",
        ),
        Index("ix_pids_email_owner_received", "owner_id", "received_at"),
        Index("ix_pids_email_owner_thread", "owner_id", "thread_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    raw_record_id: Mapped[str] = mapped_column(String(36), nullable=False)
    external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(512), nullable=False)
    sender: Mapped[str] = mapped_column(String(512), nullable=False)
    recipients: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    subject: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    labels: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, default="incoming")
    content_ref: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    source_version: Mapped[str | None] = mapped_column(String(256), nullable=True)
    processed_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_now, onupdate=_now)


class ThreadIntelligenceRow(Base):
    __tablename__ = "pids_thread_intelligence"
    __table_args__ = (
        UniqueConstraint("owner_id", "source_id", "thread_id", name="uq_pids_thread_identity"),
        ForeignKeyConstraint(
            ["owner_id", "source_id"],
            ["pids_sources.owner_id", "pids_sources.id"],
            name="fk_pids_thread_source_owner",
            ondelete="RESTRICT",
        ),
        Index("ix_pids_thread_owner_updated", "owner_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(512), nullable=False)
    analysis: Mapped[dict] = mapped_column(JSON, nullable=False)
    analysis_version: Mapped[str] = mapped_column(String(128), nullable=False)
    latest_message_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_now, onupdate=_now)


class TaskCandidateRow(Base):
    __tablename__ = "pids_task_candidates"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_pids_task_candidate_idempotency"),
        ForeignKeyConstraint(
            ["owner_id", "knowledge_id"],
            ["pids_knowledge_records.owner_id", "pids_knowledge_records.id"],
            name="fk_pids_task_candidate_knowledge_owner",
            ondelete="RESTRICT",
        ),
        Index("ix_pids_tasks_owner_state_due", "owner_id", "review_state", "due_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    knowledge_id: Mapped[str] = mapped_column(String(36), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    review_state: Mapped[str] = mapped_column(String(24), nullable=False, default="needs_review")
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_timezone: Mapped[str | None] = mapped_column(String(128), nullable=True)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_now, onupdate=_now)


class EventCandidateRow(Base):
    __tablename__ = "pids_event_candidates"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_pids_event_candidate_idempotency"),
        ForeignKeyConstraint(
            ["owner_id", "knowledge_id"],
            ["pids_knowledge_records.owner_id", "pids_knowledge_records.id"],
            name="fk_pids_event_candidate_knowledge_owner",
            ondelete="RESTRICT",
        ),
        Index("ix_pids_events_owner_start", "owner_id", "start_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    knowledge_id: Mapped[str] = mapped_column(String(36), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    start_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    event_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_timezone: Mapped[str | None] = mapped_column(String(128), nullable=True)
    all_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_state: Mapped[str] = mapped_column(String(24), nullable=False, default="needs_review")
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_now, onupdate=_now)


class MemoryItemRow(Base):
    __tablename__ = "pids_memory_items"
    __table_args__ = (
        UniqueConstraint("owner_id", "knowledge_id", name="uq_pids_memory_knowledge"),
        ForeignKeyConstraint(
            ["owner_id", "knowledge_id"],
            ["pids_knowledge_records.owner_id", "pids_knowledge_records.id"],
            name="fk_pids_memory_knowledge_owner",
            ondelete="RESTRICT",
        ),
        Index("ix_pids_memory_owner_lifecycle", "owner_id", "lifecycle_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    knowledge_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(String(16), nullable=False, default="candidate")
    valid_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_now, onupdate=_now)


class InsightRow(Base):
    __tablename__ = "pids_insights"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_pids_insight_idempotency"),
        ForeignKeyConstraint(
            ["owner_id", "knowledge_id"],
            ["pids_knowledge_records.owner_id", "pids_knowledge_records.id"],
            name="fk_pids_insight_knowledge_owner",
            ondelete="RESTRICT",
        ),
        Index("ix_pids_insights_owner_status", "owner_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    knowledge_id: Mapped[str] = mapped_column(String(36), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_now)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class RecommendationRow(Base):
    __tablename__ = "pids_recommendations"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_pids_recommendation_idempotency"),
        ForeignKeyConstraint(
            ["owner_id", "knowledge_id"],
            ["pids_knowledge_records.owner_id", "pids_knowledge_records.id"],
            name="fk_pids_recommendation_knowledge_owner",
            ondelete="RESTRICT",
        ),
        Index("ix_pids_recommendations_owner_state_priority", "owner_id", "status", "priority"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    knowledge_id: Mapped[str] = mapped_column(String(36), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    suggested_action: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_now)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class ActionApprovalRow(Base):
    __tablename__ = "pids_action_approvals"
    __table_args__ = (
        UniqueConstraint("owner_id", "request_id", name="uq_pids_action_request"),
        Index("ix_pids_action_approvals_owner_status", "owner_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_approval")
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    reviewer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_now)


class ProcessingJobRow(Base):
    __tablename__ = "pids_processing_jobs"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_pids_job_idempotency"),
        ForeignKeyConstraint(
            ["owner_id", "source_id"],
            ["pids_sources.owner_id", "pids_sources.id"],
            name="fk_pids_job_source_owner",
            ondelete="CASCADE",
        ),
        Index("ix_pids_jobs_status_next_retry", "status", "next_retry_at"),
        Index("ix_pids_jobs_owner_created", "owner_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    raw_record_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    stage: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_now, onupdate=_now)


class AuditEventRow(Base):
    __tablename__ = "pids_audit_events"
    __table_args__ = (
        Index("ix_pids_audit_owner_created", "owner_id", "created_at"),
        Index("ix_pids_audit_target", "owner_id", "target_type", "target_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[int] = mapped_column(Integer, nullable=False)
    details: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=_now)


class FeedbackRow(Base):
    __tablename__ = "pids_feedback"
    __table_args__ = (
        Index("ix_pids_feedback_owner_target", "owner_id", "target_type", "target_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[object | None] = mapped_column(JSON, nullable=True)
    snoozed_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
