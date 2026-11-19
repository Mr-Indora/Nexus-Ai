"""User-scoped repositories for isolated PIDS persistence."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from pids.contracts import (
    Deadline,
    Entity,
    EvidenceReference,
    Feedback,
    KnowledgeLifecycleState,
    KnowledgeConflict,
    KnowledgeRecord,
    KnowledgeReviewEvent,
    RawRecord,
    Relationship,
    Source,
    SourceReference,
)
from pids.persistence.database import begin_nested_transaction, utc_now
from pids.persistence.models import (
    DeadlineEvidenceRow,
    DeadlineRow,
    EntityAliasRow,
    EntityEvidenceRow,
    EntityRow,
    EvidenceReferenceRow,
    FeedbackRow,
    KnowledgeEntityRow,
    KnowledgeRecordRow,
    KnowledgeConflictRow,
    KnowledgeReviewEventRow,
    KnowledgeReviewEventRow,
    KnowledgeSourceRow,
    RawRecordRow,
    RelationshipEvidenceRow,
    RelationshipRow,
    SourceRow,
)
from pids.normalization import canonical_json, normalize_text
from pids.vault import RawDataVault, VaultIntegrityError


class PidsNotFoundError(LookupError):
    """A missing or other-owner record, intentionally indistinguishable."""


class PidsConflictError(ValueError):
    """An existing stable identity conflicts with the submitted content."""


class PidsDuplicateSourceConflict(PidsConflictError):
    """An exact source identity was replayed with different content."""


def _id(value: UUID | str) -> str:
    return str(value)


def _owner_id(owner_id: int) -> int:
    if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id <= 0:
        raise ValueError("owner_id must be a positive integer")
    return owner_id


def _source_ref(row: Any) -> SourceReference:
    return SourceReference(
        owner_id=row.owner_id,
        source_id=row.source_id,
        raw_record_id=row.raw_record_id,
        evidence_location=row.evidence_location,
    )


def _source_domain(row: SourceRow) -> Source:
    return Source(
        id=row.id,
        owner_id=row.owner_id,
        kind=row.kind,
        display_name=row.display_name,
        account_ref=row.account_ref,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _raw_domain(row: RawRecordRow) -> RawRecord:
    return RawRecord(
        id=row.id,
        owner_id=row.owner_id,
        source_id=row.source_id,
        external_id=row.external_id,
        content_sha256=row.content_sha256,
        content_ref=row.content_ref,
        payload_size_bytes=row.payload_size_bytes,
        content_type=row.content_type,
        character_encoding=row.character_encoding,
        retention_expires_at=row.retention_expires_at,
        source_timezone=row.source_timezone,
        occurred_at=row.occurred_at,
        ingested_at=row.ingested_at,
        source_version=row.source_version,
    )


def _knowledge_sources(session: Session, owner_id: int, knowledge_id: str):
    return session.scalars(
        select(KnowledgeSourceRow)
        .where(
            KnowledgeSourceRow.owner_id == owner_id,
            KnowledgeSourceRow.knowledge_id == knowledge_id,
        )
        .order_by(KnowledgeSourceRow.source_id, KnowledgeSourceRow.raw_record_id)
    ).all()


def _entity_evidence(session: Session, owner_id: int, entity_id: str):
    return session.scalars(
        select(EntityEvidenceRow)
        .where(EntityEvidenceRow.owner_id == owner_id, EntityEvidenceRow.entity_id == entity_id)
        .order_by(EntityEvidenceRow.source_id, EntityEvidenceRow.raw_record_id)
    ).all()


def _relationship_evidence(session: Session, owner_id: int, relationship_id: str):
    return session.scalars(
        select(RelationshipEvidenceRow)
        .where(
            RelationshipEvidenceRow.owner_id == owner_id,
            RelationshipEvidenceRow.relationship_id == relationship_id,
        )
        .order_by(
            RelationshipEvidenceRow.source_id, RelationshipEvidenceRow.raw_record_id
        )
    ).all()


def _deadline_evidence(session: Session, owner_id: int, deadline_id: str):
    return session.scalars(
        select(DeadlineEvidenceRow)
        .where(DeadlineEvidenceRow.owner_id == owner_id, DeadlineEvidenceRow.deadline_id == deadline_id)
        .order_by(DeadlineEvidenceRow.source_id, DeadlineEvidenceRow.raw_record_id)
    ).all()


def _knowledge_domain(session: Session, row: KnowledgeRecordRow) -> KnowledgeRecord:
    return KnowledgeRecord(
        id=row.id,
        owner_id=row.owner_id,
        record_type=row.record_type,
        data=row.data,
        source_refs=tuple(_source_ref(item) for item in _knowledge_sources(session, row.owner_id, row.id)),
        processing_version=row.processing_version,
        verification=row.verification,
        confidence=row.confidence,
        created_at=row.created_at,
        lifecycle_status=row.lifecycle_status,
        updated_at=row.updated_at,
        reviewed_at=row.reviewed_at,
        reviewer_id=row.reviewer_id,
        review_decision=row.review_decision,
        rejection_reason=row.rejection_reason,
        superseded_by_id=row.superseded_by_id,
        version=row.version,
    )


def _evidence_domain(row: EvidenceReferenceRow) -> EvidenceReference:
    return EvidenceReference(
        id=row.id,
        owner_id=row.owner_id,
        knowledge_id=row.knowledge_id,
        source_id=row.source_id,
        raw_record_id=row.raw_record_id,
        payload_sha256=row.payload_sha256,
        evidence_type=row.evidence_type,
        offset_basis=row.offset_basis,
        start_offset=row.start_offset,
        end_offset=row.end_offset,
        field_path=row.field_path,
        extraction_method=row.extraction_method,
        extraction_version=row.extraction_version,
        processed_at=row.processed_at,
        transformation=row.transformation,
        excerpt=row.excerpt,
        idempotency_key=row.idempotency_key,
    )


def _review_event_domain(row: KnowledgeReviewEventRow) -> KnowledgeReviewEvent:
    return KnowledgeReviewEvent(
        id=row.id,
        owner_id=row.owner_id,
        knowledge_id=row.knowledge_id,
        request_id=row.request_id,
        from_state=row.from_state,
        to_state=row.to_state,
        reviewer_id=row.reviewer_id,
        decision=row.decision,
        rejection_reason=row.rejection_reason,
        superseded_by_id=row.superseded_by_id,
        version_before=row.version_before,
        version_after=row.version_after,
        created_at=row.created_at,
    )


def _conflict_domain(row: KnowledgeConflictRow) -> KnowledgeConflict:
    return KnowledgeConflict(
        id=row.id,
        owner_id=row.owner_id,
        existing_id=row.existing_id,
        candidate_id=row.candidate_id,
        conflict_type=row.conflict_type,
        description=row.description,
        status=row.status,
        created_at=row.created_at,
        resolved_at=row.resolved_at,
    )


def _entity_domain(session: Session, row: EntityRow) -> Entity:
    aliases = session.scalars(
        select(EntityAliasRow.alias)
        .where(EntityAliasRow.owner_id == row.owner_id, EntityAliasRow.entity_id == row.id)
        .order_by(EntityAliasRow.alias)
    ).all()
    return Entity(
        id=row.id,
        owner_id=row.owner_id,
        entity_type=row.entity_type,
        canonical_name=row.canonical_name,
        aliases=tuple(aliases),
        evidence=tuple(_source_ref(item) for item in _entity_evidence(session, row.owner_id, row.id)),
        created_at=row.created_at,
    )


def _relationship_domain(session: Session, row: RelationshipRow) -> Relationship:
    return Relationship(
        id=row.id,
        owner_id=row.owner_id,
        subject_id=row.subject_id,
        object_id=row.object_id,
        relation_type=row.relation_type,
        evidence=tuple(
            _source_ref(item) for item in _relationship_evidence(session, row.owner_id, row.id)
        ),
        confidence=row.confidence,
        created_at=row.created_at,
    )


def _deadline_domain(session: Session, row: DeadlineRow) -> Deadline:
    return Deadline(
        id=row.id,
        owner_id=row.owner_id,
        record_id=row.record_id,
        due_at=row.due_at,
        due_date=row.due_date,
        source_timezone=row.source_timezone,
        evidence=tuple(
            _source_ref(item) for item in _deadline_evidence(session, row.owner_id, row.id)
        ),
        confidence=row.confidence,
        created_at=row.created_at,
    )


class _Repository:
    def __init__(self, session: Session):
        self.session = session

    def _get_owned(self, model, owner_id: int, record_id: UUID | str):
        owner_id = _owner_id(owner_id)
        row = self.session.scalar(
            select(model).where(model.owner_id == owner_id, model.id == _id(record_id))
        )
        if row is None:
            raise PidsNotFoundError("record not found")
        return row

    def _validate_references(self, owner_id: int, references: tuple[SourceReference, ...]):
        owner_id = _owner_id(owner_id)
        for reference in references:
            if reference.owner_id != owner_id:
                raise PidsNotFoundError("source record not found")
            raw = self.session.scalar(
                select(RawRecordRow).where(
                    RawRecordRow.owner_id == owner_id,
                    RawRecordRow.source_id == _id(reference.source_id),
                    RawRecordRow.id == _id(reference.raw_record_id),
                )
            )
            if raw is None:
                raise PidsNotFoundError("source record not found")


class SourceRepository(_Repository):
    def create(self, source: Source) -> Source:
        existing = self.session.get(SourceRow, _id(source.id))
        if existing is not None:
            if _source_domain(existing) == source:
                return source
            raise PidsConflictError("source identifier already exists")
        row = SourceRow(
            id=_id(source.id),
            owner_id=source.owner_id,
            kind=source.kind.value,
            display_name=source.display_name,
            account_ref=source.account_ref,
            status=source.status.value,
            created_at=source.created_at,
            updated_at=source.updated_at,
        )
        self.session.add(row)
        self.session.flush()
        return _source_domain(row)

    def get(self, owner_id: int, source_id: UUID | str) -> Source:
        return _source_domain(self._get_owned(SourceRow, owner_id, source_id))

    def update(self, owner_id: int, source_id: UUID | str, **changes) -> Source:
        allowed = {"display_name", "account_ref", "status"}
        if not changes or changes.keys() - allowed:
            raise ValueError("only display_name, account_ref, and status may be updated")
        row = self._get_owned(SourceRow, owner_id, source_id)
        values = _source_domain(row).model_dump()
        values.update(changes)
        values["updated_at"] = utc_now()
        updated = Source.model_validate(values)
        row.display_name = updated.display_name
        row.account_ref = updated.account_ref
        row.status = updated.status.value
        row.updated_at = updated.updated_at
        self.session.flush()
        return _source_domain(row)


class RawRecordRepository(_Repository):
    def create_idempotent(self, record: RawRecord) -> RawRecord:
        owner_id = _owner_id(record.owner_id)
        source_version = record.source_version or "default-v1"
        source = self.session.scalar(
            select(SourceRow).where(
                SourceRow.owner_id == owner_id, SourceRow.id == _id(record.source_id)
            )
        )
        if source is None:
            raise PidsNotFoundError("source not found")

        statement = (
            sqlite_insert(RawRecordRow)
            .values(
                id=_id(record.id),
                owner_id=owner_id,
                source_id=_id(record.source_id),
                external_id=record.external_id,
                content_sha256=record.content_sha256,
                content_ref=record.content_ref,
                payload_size_bytes=record.payload_size_bytes,
                content_type=record.content_type,
                character_encoding=record.character_encoding,
                retention_expires_at=record.retention_expires_at,
                source_timezone=record.source_timezone,
                occurred_at=record.occurred_at,
                ingested_at=record.ingested_at,
                source_version=source_version,
                created_at=record.ingested_at,
                updated_at=record.ingested_at,
            )
            .on_conflict_do_nothing(
                index_elements=["owner_id", "source_id", "external_id", "source_version"]
            )
        )
        self.session.execute(statement)
        existing = self.session.scalar(
            select(RawRecordRow).where(
                RawRecordRow.owner_id == owner_id,
                RawRecordRow.source_id == _id(record.source_id),
                RawRecordRow.external_id == record.external_id,
                RawRecordRow.source_version == source_version,
            )
        )
        if existing is None:
            self.session.flush()
            return record.model_copy(update={"source_version": source_version})
        if existing.content_sha256 != record.content_sha256:
            raise PidsDuplicateSourceConflict(
                "source identity already exists with different content"
            )
        return _raw_domain(existing)

    def get(self, owner_id: int, raw_record_id: UUID | str) -> RawRecord:
        return _raw_domain(self._get_owned(RawRecordRow, owner_id, raw_record_id))

    def find_content_candidates(self, owner_id: int, content_sha256: str) -> tuple[RawRecord, ...]:
        owner_id = _owner_id(owner_id)
        rows = self.session.scalars(
            select(RawRecordRow)
            .where(
                RawRecordRow.owner_id == owner_id,
                RawRecordRow.content_sha256 == content_sha256.lower(),
            )
            .order_by(RawRecordRow.source_id, RawRecordRow.external_id)
        ).all()
        return tuple(_raw_domain(row) for row in rows)

    def blob_references(self) -> set[tuple[int, str]]:
        rows = self.session.execute(
            select(RawRecordRow.owner_id, RawRecordRow.content_sha256).where(
                RawRecordRow.content_ref.is_not(None)
            )
        ).all()
        return {(owner_id, digest) for owner_id, digest in rows}

    def expired(self, now: datetime | None = None) -> tuple[RawRecord, ...]:
        now = now or utc_now()
        rows = self.session.scalars(
            select(RawRecordRow)
            .where(
                RawRecordRow.retention_expires_at.is_not(None),
                RawRecordRow.retention_expires_at <= now,
            )
            .order_by(RawRecordRow.owner_id, RawRecordRow.id)
        ).all()
        return tuple(_raw_domain(row) for row in rows)


def _knowledge_idempotency_key(record: KnowledgeRecord) -> str:
    identity = [
        "pids.knowledge-record.v1",
        record.owner_id,
        record.record_type,
        record.processing_version,
        sorted((str(ref.source_id), str(ref.raw_record_id)) for ref in record.source_refs),
    ]
    return hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class KnowledgeRecordRepository(_Repository):
    def create_idempotent(self, record: KnowledgeRecord) -> KnowledgeRecord:
        owner_id = _owner_id(record.owner_id)
        if (
            record.lifecycle_status != KnowledgeLifecycleState.CANDIDATE
            or record.version != 1
            or record.reviewed_at is not None
            or record.reviewer_id is not None
            or record.verification.value == "user_confirmed"
        ):
            raise ValueError("new knowledge records must start as unconfirmed candidates")
        self._validate_references(owner_id, record.source_refs)
        key = _knowledge_idempotency_key(record)
        statement = (
            sqlite_insert(KnowledgeRecordRow)
            .values(
                id=_id(record.id),
                owner_id=owner_id,
                record_type=record.record_type,
                data=record.data,
                idempotency_key=key,
                processing_version=record.processing_version,
                verification=record.verification.value,
                confidence=record.confidence,
                lifecycle_status=record.lifecycle_status.value,
                reviewed_at=record.reviewed_at,
                reviewer_id=record.reviewer_id,
                review_decision=record.review_decision,
                rejection_reason=record.rejection_reason,
                superseded_by_id=_id(record.superseded_by_id) if record.superseded_by_id else None,
                version=record.version,
                created_at=record.created_at,
                updated_at=record.created_at,
            )
            .on_conflict_do_nothing(index_elements=["owner_id", "idempotency_key"])
        )
        result = self.session.execute(statement)
        if result.rowcount:
            for reference in record.source_refs:
                self.session.add(
                    KnowledgeSourceRow(
                        owner_id=owner_id,
                        knowledge_id=_id(record.id),
                        source_id=_id(reference.source_id),
                        raw_record_id=_id(reference.raw_record_id),
                        evidence_location=reference.evidence_location,
                    )
                )
            self.session.flush()
            return record

        existing = self.session.scalar(
            select(KnowledgeRecordRow).where(
                KnowledgeRecordRow.owner_id == owner_id,
                KnowledgeRecordRow.idempotency_key == key,
            )
        )
        if existing is None:
            raise RuntimeError("knowledge idempotency insert was not persisted")
        persisted = _knowledge_domain(self.session, existing)
        mutable_lifecycle_fields = {
            "id",
            "lifecycle_status",
            "updated_at",
            "reviewed_at",
            "reviewer_id",
            "review_decision",
            "rejection_reason",
            "superseded_by_id",
            "version",
            "verification",
        }
        if persisted.model_dump(exclude=mutable_lifecycle_fields) != record.model_dump(
            exclude=mutable_lifecycle_fields
        ):
            raise PidsConflictError(
                "knowledge identity already exists with different derived content"
            )
        return persisted

    def get(self, owner_id: int, knowledge_id: UUID | str) -> KnowledgeRecord:
        return _knowledge_domain(
            self.session, self._get_owned(KnowledgeRecordRow, owner_id, knowledge_id)
        )

    def update(
        self, owner_id: int, knowledge_id: UUID | str, **changes
    ) -> KnowledgeRecord:
        _owner_id(owner_id)
        self._get_owned(KnowledgeRecordRow, owner_id, knowledge_id)
        raise ValueError("knowledge records are immutable; create a new candidate")

    def link_entity(
        self, owner_id: int, knowledge_id: UUID | str, entity_id: UUID | str
    ) -> None:
        owner_id = _owner_id(owner_id)
        self._get_owned(KnowledgeRecordRow, owner_id, knowledge_id)
        self._get_owned(EntityRow, owner_id, entity_id)
        key = (owner_id, _id(knowledge_id), _id(entity_id))
        if self.session.get(KnowledgeEntityRow, key) is None:
            self.session.add(
                KnowledgeEntityRow(
                    owner_id=key[0], knowledge_id=key[1], entity_id=key[2]
                )
            )
            self.session.flush()


class EvidenceReferenceRepository(_Repository):
    def _validate_payload(self, evidence: EvidenceReference, vault: RawDataVault) -> None:
        owner_id = _owner_id(evidence.owner_id)
        knowledge = self.session.scalar(
            select(KnowledgeRecordRow).where(
                KnowledgeRecordRow.owner_id == owner_id,
                KnowledgeRecordRow.id == _id(evidence.knowledge_id),
            )
        )
        raw = self.session.scalar(
            select(RawRecordRow).where(
                RawRecordRow.owner_id == owner_id,
                RawRecordRow.source_id == _id(evidence.source_id),
                RawRecordRow.id == _id(evidence.raw_record_id),
            )
        )
        source = self.session.scalar(
            select(SourceRow).where(
                SourceRow.owner_id == owner_id,
                SourceRow.id == _id(evidence.source_id),
            )
        )
        if knowledge is None or raw is None or source is None:
            raise PidsNotFoundError("evidence record not found")
        if raw.content_sha256 != evidence.payload_sha256 or raw.content_ref is None:
            raise VaultIntegrityError("evidence payload reference is invalid")
        expected_ref = vault.reference(owner_id, raw.content_sha256)
        if raw.content_ref != expected_ref:
            raise VaultIntegrityError("evidence payload reference is invalid")
        payload = vault.read(owner_id, raw.content_sha256)
        if raw.payload_size_bytes is not None and len(payload) != raw.payload_size_bytes:
            raise VaultIntegrityError("evidence payload integrity check failed")

        encoding = raw.character_encoding or "utf-8"
        try:
            decoded = payload.decode(encoding, errors="strict")
        except (LookupError, UnicodeDecodeError) as exc:
            raise VaultIntegrityError("evidence payload cannot be decoded") from exc
        if evidence.evidence_type == "text":
            if evidence.offset_basis != "normalized_text_unicode_codepoints":
                raise ValueError("unsupported text evidence offset basis")
            normalized = normalize_text(decoded)
            if evidence.end_offset is None or evidence.end_offset > len(normalized):
                raise ValueError("text evidence offsets exceed normalized content")
            normalized_digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            if evidence.transformation.get("normalized_sha256") != normalized_digest:
                raise VaultIntegrityError("normalized evidence integrity check failed")
        elif evidence.evidence_type == "structured":
            if raw.content_type != "application/json":
                raise ValueError("structured evidence requires JSON source content")
            import json

            try:
                document = json.loads(decoded)
            except json.JSONDecodeError as exc:
                raise ValueError("structured source payload is invalid JSON") from exc
            current = document
            for segment in evidence.field_path.lstrip("/").split("/"):
                key = segment.replace("~1", "/").replace("~0", "~")
                if isinstance(current, list) and key.isdigit():
                    index = int(key)
                    if index >= len(current):
                        raise ValueError("structured evidence field path does not exist")
                    current = current[index]
                elif isinstance(current, dict) and key in current:
                    current = current[key]
                else:
                    raise ValueError("structured evidence field path does not exist")
        else:
            raise ValueError("unsupported evidence type")

    def create_idempotent(
        self, evidence: EvidenceReference, vault: RawDataVault
    ) -> EvidenceReference:
        owner_id = _owner_id(evidence.owner_id)
        self._validate_payload(evidence, vault)
        try:
            canonical_json(evidence.transformation)
        except ValueError as exc:
            raise ValueError("evidence transformation metadata is invalid") from exc
        existing = self.session.scalar(
            select(EvidenceReferenceRow).where(
                EvidenceReferenceRow.owner_id == owner_id,
                EvidenceReferenceRow.knowledge_id == _id(evidence.knowledge_id),
                EvidenceReferenceRow.idempotency_key == evidence.idempotency_key,
            )
        )
        if existing is not None:
            persisted = _evidence_domain(existing)
            if persisted.model_dump(exclude={"id"}) != evidence.model_dump(exclude={"id"}):
                raise PidsConflictError("evidence identity conflicts with existing evidence")
            return persisted
        row = EvidenceReferenceRow(
            id=_id(evidence.id),
            owner_id=owner_id,
            knowledge_id=_id(evidence.knowledge_id),
            source_id=_id(evidence.source_id),
            raw_record_id=_id(evidence.raw_record_id),
            payload_sha256=evidence.payload_sha256,
            evidence_type=evidence.evidence_type,
            offset_basis=evidence.offset_basis,
            start_offset=evidence.start_offset,
            end_offset=evidence.end_offset,
            field_path=evidence.field_path,
            extraction_method=evidence.extraction_method,
            extraction_version=evidence.extraction_version,
            processed_at=evidence.processed_at,
            transformation=evidence.transformation,
            excerpt=evidence.excerpt,
            idempotency_key=evidence.idempotency_key,
        )
        self.session.add(row)
        self.session.flush()
        return _evidence_domain(row)

    def list_for_knowledge(
        self, owner_id: int, knowledge_id: UUID | str
    ) -> tuple[EvidenceReference, ...]:
        owner_id = _owner_id(owner_id)
        self._get_owned(KnowledgeRecordRow, owner_id, knowledge_id)
        rows = self.session.scalars(
            select(EvidenceReferenceRow)
            .where(
                EvidenceReferenceRow.owner_id == owner_id,
                EvidenceReferenceRow.knowledge_id == _id(knowledge_id),
            )
            .order_by(EvidenceReferenceRow.processed_at, EvidenceReferenceRow.id)
        ).all()
        return tuple(_evidence_domain(row) for row in rows)

    def validate(
        self, owner_id: int, evidence_id: UUID | str, vault: RawDataVault
    ) -> EvidenceReference:
        row = self._get_owned(EvidenceReferenceRow, owner_id, evidence_id)
        evidence = _evidence_domain(row)
        self._validate_payload(evidence, vault)
        return evidence


class KnowledgeLifecycleRepository(_Repository):
    _TRANSITIONS = {
        KnowledgeLifecycleState.CANDIDATE: {
            KnowledgeLifecycleState.REVIEWED,
            KnowledgeLifecycleState.REJECTED,
        },
        KnowledgeLifecycleState.REVIEWED: {
            KnowledgeLifecycleState.CONFIRMED,
            KnowledgeLifecycleState.REJECTED,
        },
        KnowledgeLifecycleState.CONFIRMED: {KnowledgeLifecycleState.SUPERSEDED},
        KnowledgeLifecycleState.REJECTED: set(),
        KnowledgeLifecycleState.SUPERSEDED: set(),
    }

    def create_conflict(
        self,
        owner_id: int,
        existing_id: UUID | str,
        candidate_id: UUID | str,
        conflict_type: str,
        description: str,
    ) -> KnowledgeConflict:
        owner_id = _owner_id(owner_id)
        conflict = KnowledgeConflict(
            id=uuid4(),
            owner_id=owner_id,
            existing_id=existing_id,
            candidate_id=candidate_id,
            conflict_type=conflict_type,
            description=description,
            status="open",
            created_at=utc_now(),
        )
        if conflict.existing_id == conflict.candidate_id:
            raise ValueError("a knowledge record cannot conflict with itself")
        existing = self._get_owned(KnowledgeRecordRow, owner_id, conflict.existing_id)
        candidate = self._get_owned(KnowledgeRecordRow, owner_id, conflict.candidate_id)
        if existing.lifecycle_status != KnowledgeLifecycleState.CONFIRMED.value:
            raise PidsConflictError("conflict target must be confirmed knowledge")
        if candidate.lifecycle_status not in {
            KnowledgeLifecycleState.CANDIDATE.value,
            KnowledgeLifecycleState.REVIEWED.value,
        }:
            raise PidsConflictError("conflicting knowledge must remain reviewable")
        prior = self.session.scalar(
            select(KnowledgeConflictRow).where(
                KnowledgeConflictRow.owner_id == owner_id,
                KnowledgeConflictRow.existing_id == _id(conflict.existing_id),
                KnowledgeConflictRow.candidate_id == _id(conflict.candidate_id),
            )
        )
        if prior is not None:
            result = _conflict_domain(prior)
            if (
                result.conflict_type != conflict.conflict_type
                or result.description != conflict.description
            ):
                raise PidsConflictError("conflict identity already has different details")
            return result
        row = KnowledgeConflictRow(
            id=_id(conflict.id),
            owner_id=owner_id,
            existing_id=_id(conflict.existing_id),
            candidate_id=_id(conflict.candidate_id),
            conflict_type=conflict.conflict_type,
            description=conflict.description,
            status=conflict.status,
            created_at=conflict.created_at,
        )
        self.session.add(row)
        self.session.flush()
        return _conflict_domain(row)

    def transition(
        self,
        owner_id: int,
        knowledge_id: UUID | str,
        *,
        expected_version: int,
        to_state: KnowledgeLifecycleState,
        reviewer_id: int,
        decision: str,
        request_id: str,
        rejection_reason: str | None = None,
        superseded_by_id: UUID | str | None = None,
    ) -> tuple[KnowledgeRecord, KnowledgeReviewEvent]:
        owner_id = _owner_id(owner_id)
        if isinstance(reviewer_id, bool) or not isinstance(reviewer_id, int) or reviewer_id <= 0:
            raise ValueError("reviewer identity must be a positive integer")
        to_state = KnowledgeLifecycleState(to_state)
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id.strip()) > 128:
            raise ValueError("review request_id must contain 1 to 128 characters")
        if not isinstance(decision, str) or not decision.strip() or len(decision.strip()) > 64:
            raise ValueError("review decision must contain 1 to 64 characters")
        if rejection_reason is not None:
            if not isinstance(rejection_reason, str) or len(rejection_reason.strip()) > 1024:
                raise ValueError("rejection_reason must not exceed 1024 characters")
            rejection_reason = rejection_reason.strip()
        request_id = request_id.strip()
        decision = decision.strip()
        existing_event = self.session.scalar(
            select(KnowledgeReviewEventRow).where(
                KnowledgeReviewEventRow.owner_id == owner_id,
                KnowledgeReviewEventRow.knowledge_id == _id(knowledge_id),
                KnowledgeReviewEventRow.request_id == request_id,
            )
        )
        if existing_event is not None:
            event = _review_event_domain(existing_event)
            if (
                event.reviewer_id != reviewer_id
                or event.to_state != to_state
                or event.decision != decision
                or event.rejection_reason != rejection_reason
                or event.superseded_by_id != (UUID(str(superseded_by_id)) if superseded_by_id else None)
            ):
                raise PidsConflictError("review request_id was reused with different values")
            return self._knowledge( owner_id, knowledge_id), event

        record = self._get_owned(KnowledgeRecordRow, owner_id, knowledge_id)
        from_state = KnowledgeLifecycleState(record.lifecycle_status)
        if record.version != expected_version:
            raise PidsConflictError("knowledge record version is stale")
        if to_state not in self._TRANSITIONS[from_state]:
            raise PidsConflictError("knowledge lifecycle transition is not allowed")
        if to_state == KnowledgeLifecycleState.REJECTED and not rejection_reason:
            raise ValueError("rejection requires a reason")
        successor_id = _id(superseded_by_id) if superseded_by_id else None
        if to_state == KnowledgeLifecycleState.SUPERSEDED:
            if successor_id is None:
                raise ValueError("supersession requires a successor record")
            successor = self._get_owned(KnowledgeRecordRow, owner_id, successor_id)
            if successor.lifecycle_status != KnowledgeLifecycleState.CONFIRMED.value:
                raise PidsConflictError("superseding record must be confirmed")
            conflict = self.session.scalar(
                select(KnowledgeConflictRow).where(
                    KnowledgeConflictRow.owner_id == owner_id,
                    KnowledgeConflictRow.existing_id == _id(knowledge_id),
                    KnowledgeConflictRow.candidate_id == successor_id,
                    KnowledgeConflictRow.status == "open",
                )
            )
            if conflict is None:
                raise PidsConflictError("explicit open conflict is required for supersession")

        now = utc_now()
        next_version = expected_version + 1
        with begin_nested_transaction(self.session):
            changed = self.session.execute(
                update(KnowledgeRecordRow)
                .where(
                    KnowledgeRecordRow.owner_id == owner_id,
                    KnowledgeRecordRow.id == _id(knowledge_id),
                    KnowledgeRecordRow.version == expected_version,
                    KnowledgeRecordRow.lifecycle_status == from_state.value,
                )
                .values(
                    lifecycle_status=to_state.value,
                    verification=(
                        "user_confirmed"
                        if to_state == KnowledgeLifecycleState.CONFIRMED
                        else record.verification
                    ),
                    reviewed_at=now,
                    reviewer_id=reviewer_id,
                    review_decision=decision,
                    rejection_reason=rejection_reason,
                    superseded_by_id=successor_id,
                    version=next_version,
                    updated_at=now,
                )
            )
            if changed.rowcount != 1:
                raise PidsConflictError("knowledge record version is stale")
            event_row = KnowledgeReviewEventRow(
                id=_id(uuid4()),
                owner_id=owner_id,
                knowledge_id=_id(knowledge_id),
                request_id=request_id,
                from_state=from_state.value,
                to_state=to_state.value,
                reviewer_id=reviewer_id,
                decision=decision,
                rejection_reason=rejection_reason,
                superseded_by_id=successor_id,
                version_before=expected_version,
                version_after=next_version,
                created_at=now,
            )
            self.session.add(event_row)
            if to_state == KnowledgeLifecycleState.SUPERSEDED:
                conflict.resolved_at = now
                conflict.status = "resolved"
            self.session.flush()
        return self._knowledge(owner_id, knowledge_id), _review_event_domain(event_row)

    def _knowledge(self, owner_id: int, knowledge_id: UUID | str) -> KnowledgeRecord:
        return _knowledge_domain(
            self.session, self._get_owned(KnowledgeRecordRow, owner_id, knowledge_id)
        )

    def list_events(
        self, owner_id: int, knowledge_id: UUID | str
    ) -> tuple[KnowledgeReviewEvent, ...]:
        owner_id = _owner_id(owner_id)
        self._get_owned(KnowledgeRecordRow, owner_id, knowledge_id)
        rows = self.session.scalars(
            select(KnowledgeReviewEventRow)
            .where(
                KnowledgeReviewEventRow.owner_id == owner_id,
                KnowledgeReviewEventRow.knowledge_id == _id(knowledge_id),
            )
            .order_by(KnowledgeReviewEventRow.version_after)
        ).all()
        return tuple(_review_event_domain(row) for row in rows)


class EntityRepository(_Repository):
    def list_owned(self, owner_id: int, *, limit: int = 200) -> tuple[Entity, ...]:
        owner_id = _owner_id(owner_id)
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        rows = self.session.scalars(
            select(EntityRow)
            .where(EntityRow.owner_id == owner_id)
            .order_by(EntityRow.entity_type, EntityRow.canonical_name, EntityRow.id)
            .limit(limit)
        ).all()
        return tuple(_entity_domain(self.session, row) for row in rows)

    def create(self, entity: Entity) -> Entity:
        self._validate_references(entity.owner_id, entity.evidence)
        existing = self.session.get(EntityRow, _id(entity.id))
        if existing is not None:
            persisted = _entity_domain(self.session, existing)
            if persisted == entity:
                return persisted
            raise PidsConflictError("entity identifier already exists with different content")
        row = EntityRow(
            id=_id(entity.id),
            owner_id=entity.owner_id,
            entity_type=entity.entity_type,
            canonical_name=entity.canonical_name,
            created_at=entity.created_at,
            updated_at=entity.created_at,
        )
        self.session.add(row)
        self.session.flush()
        for alias in entity.aliases:
            self.session.add(EntityAliasRow(owner_id=entity.owner_id, entity_id=row.id, alias=alias))
        for reference in entity.evidence:
            self.session.add(
                EntityEvidenceRow(
                    owner_id=entity.owner_id,
                    entity_id=row.id,
                    source_id=_id(reference.source_id),
                    raw_record_id=_id(reference.raw_record_id),
                    evidence_location=reference.evidence_location,
                )
            )
        self.session.flush()
        return _entity_domain(self.session, row)

    def add_evidence(
        self, owner_id: int, entity_id: UUID | str, reference: SourceReference
    ) -> None:
        owner_id = _owner_id(owner_id)
        if reference.owner_id != owner_id:
            raise PidsNotFoundError("source record not found")
        self._get_owned(EntityRow, owner_id, entity_id)
        self._validate_references(owner_id, (reference,))
        key = (
            owner_id,
            _id(entity_id),
            _id(reference.source_id),
            _id(reference.raw_record_id),
        )
        if self.session.get(EntityEvidenceRow, key) is None:
            self.session.add(
                EntityEvidenceRow(
                    owner_id=key[0],
                    entity_id=key[1],
                    source_id=key[2],
                    raw_record_id=key[3],
                    evidence_location=reference.evidence_location,
                )
            )
            self.session.flush()

    def get(self, owner_id: int, entity_id: UUID | str) -> Entity:
        return _entity_domain(self.session, self._get_owned(EntityRow, owner_id, entity_id))

    def update(self, owner_id: int, entity_id: UUID | str, **changes) -> Entity:
        allowed = {"canonical_name", "aliases"}
        if not changes or changes.keys() - allowed:
            raise ValueError("only canonical_name and aliases may be updated")
        row = self._get_owned(EntityRow, owner_id, entity_id)
        values = _entity_domain(self.session, row).model_dump()
        values.update(changes)
        updated = Entity.model_validate(values)
        row.canonical_name = updated.canonical_name
        row.updated_at = utc_now()
        self.session.execute(
            delete(EntityAliasRow).where(
                EntityAliasRow.owner_id == _owner_id(owner_id),
                EntityAliasRow.entity_id == _id(entity_id),
            )
        )
        for alias in updated.aliases:
            self.session.add(
                EntityAliasRow(
                    owner_id=_owner_id(owner_id), entity_id=_id(entity_id), alias=alias
                )
            )
        self.session.flush()
        return _entity_domain(self.session, row)


class RelationshipRepository(_Repository):
    def create(self, relationship: Relationship) -> Relationship:
        self._validate_references(relationship.owner_id, relationship.evidence)
        self._get_owned(EntityRow, relationship.owner_id, relationship.subject_id)
        self._get_owned(EntityRow, relationship.owner_id, relationship.object_id)
        existing = self.session.get(RelationshipRow, _id(relationship.id))
        if existing is not None:
            persisted = _relationship_domain(self.session, existing)
            if persisted == relationship:
                return persisted
            raise PidsConflictError("relationship identifier already exists with different content")
        row = RelationshipRow(
            id=_id(relationship.id),
            owner_id=relationship.owner_id,
            subject_id=_id(relationship.subject_id),
            object_id=_id(relationship.object_id),
            relation_type=relationship.relation_type,
            confidence=relationship.confidence,
            created_at=relationship.created_at,
            updated_at=relationship.created_at,
        )
        self.session.add(row)
        self.session.flush()
        for reference in relationship.evidence:
            self.session.add(
                RelationshipEvidenceRow(
                    owner_id=relationship.owner_id,
                    relationship_id=row.id,
                    source_id=_id(reference.source_id),
                    raw_record_id=_id(reference.raw_record_id),
                    evidence_location=reference.evidence_location,
                )
            )
        self.session.flush()
        return _relationship_domain(self.session, row)

    def get(self, owner_id: int, relationship_id: UUID | str) -> Relationship:
        return _relationship_domain(
            self.session, self._get_owned(RelationshipRow, owner_id, relationship_id)
        )


class DeadlineRepository(_Repository):
    def create(self, deadline: Deadline) -> Deadline:
        self._validate_references(deadline.owner_id, deadline.evidence)
        self._get_owned(KnowledgeRecordRow, deadline.owner_id, deadline.record_id)
        existing = self.session.get(DeadlineRow, _id(deadline.id))
        if existing is not None:
            persisted = _deadline_domain(self.session, existing)
            if persisted == deadline:
                return persisted
            raise PidsConflictError("deadline identifier already exists with different content")
        row = DeadlineRow(
            id=_id(deadline.id),
            owner_id=deadline.owner_id,
            record_id=_id(deadline.record_id),
            due_at=deadline.due_at,
            due_date=deadline.due_date,
            source_timezone=deadline.source_timezone,
            confidence=deadline.confidence,
            created_at=deadline.created_at,
            updated_at=deadline.created_at,
        )
        self.session.add(row)
        self.session.flush()
        for reference in deadline.evidence:
            self.session.add(
                DeadlineEvidenceRow(
                    owner_id=deadline.owner_id,
                    deadline_id=row.id,
                    source_id=_id(reference.source_id),
                    raw_record_id=_id(reference.raw_record_id),
                    evidence_location=reference.evidence_location,
                )
            )
        self.session.flush()
        return _deadline_domain(self.session, row)

    def get(self, owner_id: int, deadline_id: UUID | str) -> Deadline:
        return _deadline_domain(self.session, self._get_owned(DeadlineRow, owner_id, deadline_id))

    def update(self, owner_id: int, deadline_id: UUID | str, **changes) -> Deadline:
        allowed = {"due_at", "due_date", "source_timezone", "confidence"}
        if not changes or changes.keys() - allowed:
            raise ValueError(
                "only due_at, due_date, source_timezone, and confidence may be updated"
            )
        row = self._get_owned(DeadlineRow, owner_id, deadline_id)
        values = _deadline_domain(self.session, row).model_dump()
        values.update(changes)
        updated = Deadline.model_validate(values)
        row.due_at = updated.due_at
        row.due_date = updated.due_date
        row.source_timezone = updated.source_timezone
        row.confidence = updated.confidence
        row.updated_at = utc_now()
        self.session.flush()
        return _deadline_domain(self.session, row)


class FeedbackRepository(_Repository):
    def create(self, feedback: Feedback) -> Feedback:
        existing = self.session.get(FeedbackRow, _id(feedback.id))
        if existing is not None:
            persisted = self.get(feedback.owner_id, feedback.id)
            if persisted == feedback:
                return persisted
            raise PidsConflictError("feedback identifier already exists with different content")
        row = FeedbackRow(
            id=_id(feedback.id),
            owner_id=feedback.owner_id,
            target_type=feedback.target_type,
            target_id=feedback.target_id,
            action=feedback.action.value,
            value=feedback.value,
            snoozed_until=feedback.snoozed_until,
            occurred_at=feedback.occurred_at,
            created_at=feedback.occurred_at,
            updated_at=feedback.occurred_at,
        )
        self.session.add(row)
        self.session.flush()
        return feedback

    def get(self, owner_id: int, feedback_id: UUID | str) -> Feedback:
        row = self._get_owned(FeedbackRow, owner_id, feedback_id)
        fields = dict(
            id=row.id,
            owner_id=row.owner_id,
            target_type=row.target_type,
            target_id=row.target_id,
            action=row.action,
            snoozed_until=row.snoozed_until,
            occurred_at=row.occurred_at,
        )
        if row.action == "correct":
            fields["value"] = row.value
        return Feedback(**fields)


class PidsRepositories:
    """Convenience bundle; every operation remains scoped to an owner ID."""

    def __init__(self, session: Session):
        self.sources = SourceRepository(session)
        self.raw_records = RawRecordRepository(session)
        self.knowledge_records = KnowledgeRecordRepository(session)
        self.evidence = EvidenceReferenceRepository(session)
        self.knowledge_lifecycle = KnowledgeLifecycleRepository(session)
        self.entities = EntityRepository(session)
        self.relationships = RelationshipRepository(session)
        self.deadlines = DeadlineRepository(session)
        self.feedback = FeedbackRepository(session)
