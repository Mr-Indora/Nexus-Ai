"""Bounded deterministic ingestion-to-knowledge pipeline for isolated PIDS storage."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from datetime import datetime
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field, JsonValue
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from pids.contracts import (
    Contract,
    Deadline,
    Entity,
    EvidenceReference,
    KnowledgeRecord,
    RawRecord,
    Relationship,
    Source,
    SourceReference,
    SourceStatus,
    VerificationStatus,
    content_sha256,
    idempotency_key,
)
from pids.normalization import IncomingRecord, normalize_record
from pids.persistence.database import begin_nested_transaction, utc_now
from pids.persistence.repositories import (
    PidsConflictError,
    PidsDuplicateSourceConflict,
    PidsNotFoundError,
    PidsRepositories,
)
from pids.sources import ConnectorError, SourceRegistry, TextConnector
from pids.understanding import EntityMention, RULE_VERSION, UnderstandingResult, understand
from pids.vault import RawDataVault, VaultError

MAX_BATCH_SIZE = 50


class EntityResolutionLimitError(ValueError):
    pass


class RecordResult(Contract):
    index: int = Field(ge=0)
    status: Literal["new", "replay", "duplicate_content", "failed"]
    raw_record_id: UUID | None = None
    knowledge_record_id: UUID | None = None
    task_count: int = 0
    deadline_count: int = 0
    entity_count: int = 0
    relationship_count: int = 0
    evidence_count: int = 0
    possible_entity_match_count: int = 0
    entity_matches: tuple[dict[str, JsonValue], ...] = ()
    error_code: str | None = None
    retryable: bool = False


class PipelineResult(Contract):
    owner_id: int = Field(gt=0, strict=True)
    source_id: UUID
    records: tuple[RecordResult, ...] = ()
    source_error_code: str | None = None
    source_retryable: bool = False

    @property
    def counts(self) -> dict[str, int]:
        return {
            "received": len(self.records),
            "new": sum(item.status == "new" for item in self.records),
            "replayed": sum(item.status == "replay" for item in self.records),
            "duplicate_content": sum(
                item.status == "duplicate_content" for item in self.records
            ),
            "failed": sum(item.status == "failed" for item in self.records),
            "tasks": sum(item.task_count for item in self.records),
            "deadlines": sum(item.deadline_count for item in self.records),
            "entities": sum(item.entity_count for item in self.records),
            "relationships": sum(item.relationship_count for item in self.records),
        }


def _stable_uuid(value: str) -> UUID:
    return uuid5(NAMESPACE_URL, value)


def _name_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _identifier_key(entity_type: str, value: str) -> str:
    normalized = _name_key(value).rstrip("/")
    if entity_type == "url":
        normalized = re.sub(r"^(https?)://", "", normalized)
    return normalized


def _possible_name(left: str, right: str) -> bool:
    left_words = set(_name_key(left).split())
    right_words = set(_name_key(right).split())
    if len(left_words) < 2 or len(right_words) < 2:
        return False
    overlap = len(left_words & right_words)
    return overlap >= 2 and overlap / len(left_words | right_words) >= 0.6


def _candidate_data(result: UnderstandingResult) -> dict:
    return {
        "rule_version": RULE_VERSION,
        "review_status": "needs_review",
        "tasks": [item.model_dump(mode="json") for item in result.tasks],
        "deadlines": [item.model_dump(mode="json") for item in result.deadlines],
        "events": [item.model_dump(mode="json") for item in result.events],
        "relationships": [item.model_dump(mode="json") for item in result.relationships],
        "facts": [item.model_dump(mode="json") for item in result.facts],
        "entities": [item.model_dump(mode="json") for item in result.entities],
    }


class IntelligencePipeline:
    def __init__(
        self,
        session: Session,
        *,
        max_batch_size: int = MAX_BATCH_SIZE,
        vault: RawDataVault | None = None,
    ):
        if not 1 <= max_batch_size <= MAX_BATCH_SIZE:
            raise ValueError(f"max_batch_size must be between 1 and {MAX_BATCH_SIZE}")
        self.session = session
        self.repositories = PidsRepositories(session)
        self.registry = SourceRegistry(self.repositories.sources)
        self.max_batch_size = max_batch_size
        self.vault = vault or RawDataVault()

    def run(
        self,
        owner_id: int,
        source_id: UUID,
        connector: TextConnector,
        *,
        cursor: str | None = None,
    ) -> PipelineResult:
        source = self.registry.get(owner_id, source_id)
        if source.status.value != "active":
            raise ValueError("source is not active")
        if connector.kind != source.kind:
            raise ValueError("connector kind does not match registered source")
        capabilities = frozenset(connector.capabilities)
        if capabilities - {"read", "pagination"}:
            raise ValueError("connector declares an unsupported capability")
        if "read" not in capabilities:
            raise ValueError("connector does not declare read capability")
        records: list[IncomingRecord] = []
        source_error = None
        source_retryable = False
        try:
            iterator = iter(connector.fetch(source, cursor=cursor))
            for _ in range(self.max_batch_size + 1):
                try:
                    records.append(next(iterator))
                except StopIteration:
                    break
        except ConnectorError as exc:
            source_error = exc.code
            source_retryable = exc.retryable
        result = self.process(owner_id, source, records)
        return result.model_copy(
            update={
                "source_error_code": source_error,
                "source_retryable": source_retryable,
            }
        )

    def process(
        self, owner_id: int, source: Source, records: Iterable[IncomingRecord]
    ) -> PipelineResult:
        if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id <= 0:
            raise ValueError("owner_id must be a positive integer")
        source = self.registry.get(owner_id, source.id)
        if source.owner_id != owner_id:
            raise PidsNotFoundError("source not found")
        if source.status != SourceStatus.ACTIVE:
            raise ValueError("source is not active")
        results: list[RecordResult] = []
        for index, value in enumerate(records):
            if index >= self.max_batch_size:
                results.append(
                    RecordResult(
                        index=index,
                        status="failed",
                        error_code="batch_limit",
                        retryable=False,
                    )
                )
                break
            try:
                incoming = IncomingRecord.model_validate(value)
                results.append(self._process_one(index, owner_id, source, incoming))
            except PidsDuplicateSourceConflict:
                results.append(
                    RecordResult(
                        index=index, status="failed", error_code="source_identity_conflict"
                    )
                )
            except PidsNotFoundError:
                results.append(
                    RecordResult(index=index, status="failed", error_code="ownership_or_reference")
                )
            except PidsConflictError:
                results.append(
                    RecordResult(index=index, status="failed", error_code="derived_record_conflict")
                )
            except EntityResolutionLimitError:
                results.append(
                    RecordResult(
                        index=index, status="failed", error_code="entity_catalog_limit"
                    )
                )
            except VaultError:
                results.append(
                    RecordResult(
                        index=index, status="failed", error_code="raw_vault_integrity"
                    )
                )
            except (ValueError, TypeError):
                results.append(
                    RecordResult(index=index, status="failed", error_code="invalid_record")
                )
            except IntegrityError:
                results.append(
                    RecordResult(
                        index=index,
                        status="failed",
                        error_code="integrity_failure",
                        retryable=False,
                    )
                )
            except SQLAlchemyError:
                results.append(
                    RecordResult(
                        index=index,
                        status="failed",
                        error_code="persistence_failure",
                        retryable=True,
                    )
                )
        return PipelineResult(owner_id=owner_id, source_id=source.id, records=tuple(results))

    def _process_one(
        self, index: int, owner_id: int, source: Source, incoming: IncomingRecord
    ) -> RecordResult:
        if incoming.owner_id != owner_id or incoming.source_id != source.id:
            raise PidsNotFoundError("source record not found")
        normalized = normalize_record(incoming)
        payload = incoming.original_payload
        original_digest = content_sha256(payload)
        external_id = incoming.external_id or f"content-sha256:{original_digest}"
        record_key = idempotency_key(owner_id, source.id, external_id)
        raw_id = _stable_uuid(f"pids-raw-v1:{record_key}")
        now = utc_now()
        raw = RawRecord(
            id=raw_id,
            owner_id=owner_id,
            source_id=source.id,
            external_id=external_id,
            content_sha256=original_digest,
            source_timezone=incoming.source_timezone,
            occurred_at=incoming.occurred_at,
            ingested_at=now,
            source_version=incoming.source_version,
        )
        with begin_nested_transaction(self.session):
            stored_digest, content_ref, payload_size = self.vault.store(
                owner_id, payload, encrypt=True
            )
            if stored_digest != original_digest:
                raise VaultError("raw payload digest changed during storage")
            raw = raw.model_copy(
                update={
                    "content_ref": content_ref,
                    "payload_size_bytes": payload_size,
                    "content_type": incoming.content_type,
                    "character_encoding": incoming.character_encoding,
                    "retention_expires_at": incoming.retention_expires_at,
                }
            )
            prior_candidates = self.repositories.raw_records.find_content_candidates(
                owner_id, raw.content_sha256
            )
            persisted_raw = self.repositories.raw_records.create_idempotent(raw)
            source_ref = SourceReference(
                owner_id=owner_id,
                source_id=source.id,
                raw_record_id=persisted_raw.id,
                evidence_location="normalized_text",
            )
            reference_time = incoming.occurred_at
            extracted = understand(
                normalized.text,
                reference_time=reference_time,
                source_timezone=incoming.source_timezone,
            )
            entity_map, entity_matches = self._resolve_entities(
                owner_id, source, persisted_raw.id, extracted
            )
            knowledge = KnowledgeRecord(
                id=_stable_uuid(
                    f"pids-knowledge-v1:{owner_id}:{persisted_raw.id}:{RULE_VERSION}"
                ),
                owner_id=owner_id,
                record_type="understanding_bundle",
                data={
                    "normalization_version": "pids-text-v1",
                    "normalized_sha256": normalized.normalized_sha256,
                    "source_record_type": incoming.record_type,
                    **_candidate_data(extracted),
                },
                source_refs=(source_ref,),
                processing_version=RULE_VERSION,
                verification=VerificationStatus.NEEDS_REVIEW,
                created_at=persisted_raw.ingested_at,
            )
            knowledge = self.repositories.knowledge_records.create_idempotent(knowledge)
            evidence_count = self._persist_evidence(
                owner_id,
                source,
                persisted_raw,
                knowledge,
                normalized.text,
                extracted,
                normalized.normalized_sha256,
            )
            for entity_id in entity_map.values():
                self.repositories.knowledge_records.link_entity(
                    owner_id, knowledge.id, entity_id
                )
            deadline_count = self._persist_resolved_deadlines(
                owner_id,
                source,
                persisted_raw.id,
                knowledge.id,
                extracted,
                incoming,
                persisted_raw.ingested_at,
            )
            relationship_count = self._persist_explicit_relationships(
                owner_id, source, persisted_raw.id, extracted, entity_map, persisted_raw.ingested_at
            )

        same_identity = any(
            item.source_id == source.id and item.external_id == external_id
            for item in prior_candidates
        )
        if same_identity:
            status = "replay"
        elif prior_candidates:
            status = "duplicate_content"
        else:
            status = "new"
        return RecordResult(
            index=index,
            status=status,
            raw_record_id=persisted_raw.id,
            knowledge_record_id=knowledge.id,
            task_count=len(extracted.tasks),
            deadline_count=deadline_count,
            entity_count=len(entity_map),
            relationship_count=relationship_count,
            evidence_count=evidence_count,
            possible_entity_match_count=sum(
                item["match_type"] == "possible" for item in entity_matches
            ),
            entity_matches=tuple(entity_matches),
        )

    def _persist_evidence(
        self,
        owner_id: int,
        source: Source,
        raw: RawRecord,
        knowledge: KnowledgeRecord,
        normalized_text: str,
        extracted: UnderstandingResult,
        normalized_sha256: str,
    ) -> int:
        candidates = [
            *extracted.tasks,
            *extracted.deadlines,
            *extracted.events,
            *extracted.relationships,
            *extracted.facts,
        ]
        evidence_specs = [
            (candidate.kind, candidate.start, candidate.end, candidate.rule)
            for candidate in candidates
        ]
        evidence_specs.extend(
            (
                f"entity:{mention.entity_type}",
                mention.start,
                mention.end,
                mention.rule,
            )
            for mention in extracted.entities
        )
        for evidence_type, start, end, version in evidence_specs:
            if start < 0 or end <= start or end > len(normalized_text):
                raise ValueError("extracted evidence offsets are invalid")
            identity = [
                "pids.evidence.v1",
                owner_id,
                str(knowledge.id),
                str(raw.id),
                evidence_type,
                start,
                end,
                version,
            ]
            key = hashlib.sha256(
                json.dumps(identity, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            reference = EvidenceReference(
                id=_stable_uuid(f"pids-evidence-v1:{key}"),
                owner_id=owner_id,
                knowledge_id=knowledge.id,
                source_id=source.id,
                raw_record_id=raw.id,
                payload_sha256=raw.content_sha256,
                evidence_type="text",
                offset_basis="normalized_text_unicode_codepoints",
                start_offset=start,
                end_offset=end,
                extraction_method="deterministic_rules",
                extraction_version=version,
                processed_at=raw.ingested_at,
                transformation={
                    "normalization_version": "pids-text-v1",
                    "normalized_sha256": normalized_sha256,
                    "source_content_type": raw.content_type or "text/plain",
                    "source_encoding": raw.character_encoding or "utf-8",
                },
                excerpt=normalized_text[start:end][:512],
                idempotency_key=key,
            )
            self.repositories.evidence.create_idempotent(reference, self.vault)
        return len(evidence_specs)

    def _resolve_entities(
        self,
        owner_id: int,
        source: Source,
        raw_id: UUID,
        extracted: UnderstandingResult,
    ) -> tuple[dict[tuple[str, str], UUID], list[dict[str, JsonValue]]]:
        entity_ids: dict[tuple[str, str], UUID] = {}
        decisions: list[dict[str, JsonValue]] = []
        mentions = list(extracted.entities)
        for relationship in extracted.relationships:
            for field, entity_type in (("subject", "person"), ("object", "organization")):
                name = str(relationship.fields[field])
                mentions.append(
                    EntityMention(
                        entity_type=entity_type,
                        canonical_name=name,
                        evidence=relationship.evidence,
                        start=relationship.start,
                        end=relationship.end,
                    )
                )
        for mention in mentions:
            reference = SourceReference(
                owner_id=owner_id,
                source_id=source.id,
                raw_record_id=raw_id,
                evidence_location=f"normalized_text:{mention.start}-{mention.end}",
            )
            name_key = _identifier_key(mention.entity_type, mention.canonical_name)
            match_key = (mention.entity_type, name_key)
            if match_key in entity_ids:
                continue
            existing = self.repositories.entities.list_owned(owner_id, limit=500)
            same_type = [item for item in existing if item.entity_type == mention.entity_type]
            canonical_match = next(
                (item for item in same_type if _identifier_key(item.entity_type, item.canonical_name) == name_key),
                None,
            )
            alias_match = next(
                (
                    item
                    for item in same_type
                    if any(_identifier_key(item.entity_type, alias) == name_key for alias in item.aliases)
                ),
                None,
            )
            possible = tuple(
                sorted(
                    (
                        item
                        for item in same_type
                        if _identifier_key(item.entity_type, item.canonical_name) != name_key
                        and _possible_name(item.canonical_name, mention.canonical_name)
                    ),
                    key=lambda item: (item.canonical_name.casefold(), str(item.id)),
                )
            )
            if canonical_match:
                entity = canonical_match
                match_type, reason = "exact", "normalized_canonical_identity"
            elif alias_match:
                entity = alias_match
                match_type, reason = "strong", "exact_owner_scoped_alias"
            else:
                if len(existing) == 500:
                    raise EntityResolutionLimitError(
                        "entity catalog exceeds the bounded resolution window"
                    )
                entity_id = _stable_uuid(
                    "pids-entity-v1:"
                    f"{owner_id}:{mention.entity_type}:"
                    f"{hashlib.sha256(name_key.encode('utf-8')).hexdigest()}"
                )
                entity = Entity(
                    id=entity_id,
                    owner_id=owner_id,
                    entity_type=mention.entity_type,
                    canonical_name=mention.canonical_name,
                    aliases=(),
                    evidence=(reference,),
                    created_at=utc_now(),
                )
                self.repositories.entities.create(entity)
                if possible:
                    match_type, reason = "possible", "name_overlap_requires_review"
                else:
                    match_type, reason = "new", "no_owner_scoped_match"
            self.repositories.entities.add_evidence(owner_id, entity.id, reference)
            entity_ids[match_key] = entity.id
            decisions.append(
                {
                    "entity_type": mention.entity_type,
                    "canonical_name": mention.canonical_name,
                    "entity_id": str(entity.id),
                    "match_type": match_type,
                    "reason": reason,
                    "possible_entity_ids": [str(item.id) for item in possible],
                }
            )
        return entity_ids, decisions

    def _persist_resolved_deadlines(
        self,
        owner_id: int,
        source: Source,
        raw_id: UUID,
        knowledge_id: UUID,
        extracted: UnderstandingResult,
        incoming: IncomingRecord,
        processed_at: datetime,
    ) -> int:
        resolved = 0
        seen_dates: set[str] = set()
        for candidate in extracted.deadlines:
            if candidate.fields.get("deadline_signal") is not True:
                continue
            due_date = candidate.fields.get("due_date")
            if not isinstance(due_date, str) or due_date in seen_dates:
                continue
            seen_dates.add(due_date)
            reference = SourceReference(
                owner_id=owner_id,
                source_id=source.id,
                raw_record_id=raw_id,
                evidence_location=f"normalized_text:{candidate.start}-{candidate.end}",
            )
            deadline = Deadline(
                id=_stable_uuid(
                    f"pids-deadline-v1:{owner_id}:{raw_id}:{due_date}:"
                    f"{candidate.fields.get('expression')}"
                ),
                owner_id=owner_id,
                record_id=knowledge_id,
                due_date=datetime.fromisoformat(due_date).date(),
                source_timezone=incoming.source_timezone,
                evidence=(reference,),
                created_at=processed_at,
            )
            deadline = self.repositories.deadlines.create(deadline)
            if deadline.id:
                resolved += 1
        return resolved

    def _persist_explicit_relationships(
        self,
        owner_id: int,
        source: Source,
        raw_id: UUID,
        extracted: UnderstandingResult,
        entity_map: dict[tuple[str, str], UUID],
        processed_at: datetime,
    ) -> int:
        created = 0
        seen_relationships: set[tuple[str, UUID, UUID]] = set()
        for candidate in extracted.relationships:
            subject_name = str(candidate.fields["subject"])
            object_name = str(candidate.fields["object"])
            subject_id = entity_map.get(("person", _identifier_key("person", subject_name)))
            object_id = entity_map.get(
                ("organization", _identifier_key("organization", object_name))
            )
            if subject_id is None or object_id is None:
                continue
            reference = SourceReference(
                owner_id=owner_id,
                source_id=source.id,
                raw_record_id=raw_id,
                evidence_location=f"normalized_text:{candidate.start}-{candidate.end}",
            )
            relation_type = str(candidate.fields["relation_type"])
            relation_type = str(candidate.fields["relation_type"])
            identity = (relation_type, subject_id, object_id)
            if identity in seen_relationships:
                continue
            seen_relationships.add(identity)
            relationship = Relationship(
                id=_stable_uuid(
                    f"pids-relationship-v1:{owner_id}:{raw_id}:"
                    f"{relation_type}:{subject_id}:{object_id}"
                ),
                owner_id=owner_id,
                subject_id=subject_id,
                object_id=object_id,
                relation_type=relation_type,
                evidence=(reference,),
                confidence=None,
                created_at=processed_at,
            )
            self.repositories.relationships.create(relationship)
            created += 1
        return created
