from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from pids.contracts import Entity, SourceKind, SourceReference, SourceStatus, content_sha256
from pids.normalization import IncomingRecord, canonical_json, normalize_record, normalize_text
from pids.persistence.database import (
    ISOLATED_WORKSPACE,
    create_pids_engine,
    create_session_factory,
)
from pids.persistence.models import (
    DeadlineRow,
    EntityRow,
    KnowledgeRecordRow,
    RawRecordRow,
    RelationshipRow,
)
from pids.persistence.repositories import PidsNotFoundError, PidsRepositories
from pids.pipeline import IntelligencePipeline
from pids.sources import ConnectorError, SourceRegistry
from pids.understanding import understand
from pids.vault import RawDataVault


UTC = timezone.utc
NOW = datetime(2026, 11, 12, 4, 0, tzinfo=UTC)
ALEMBIC_CONFIG = Path(__file__).resolve().parents[1] / "pids" / "persistence" / "alembic.ini"
TEST_VAULT_ROOT = None


@pytest.fixture(scope="module")
def isolated_session():
    global TEST_VAULT_ROOT
    ISOLATED_WORKSPACE.mkdir(parents=True, exist_ok=True)
    path = ISOLATED_WORKSPACE / f"test-engine-{uuid4().hex}.db"
    TEST_VAULT_ROOT = ISOLATED_WORKSPACE / f"test-engine-vault-{uuid4().hex}"
    url = f"sqlite:///{path.as_posix()}"
    config = Config(str(ALEMBIC_CONFIG))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine = create_pids_engine(url)
    session = create_session_factory(engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            generated = Path(f"{path}{suffix}")
            if generated.exists():
                generated.unlink()
        if TEST_VAULT_ROOT.exists():
            shutil.rmtree(TEST_VAULT_ROOT)


def _pipeline(session, **kwargs):
    return IntelligencePipeline(
        session, vault=RawDataVault(TEST_VAULT_ROOT), **kwargs
    )


def register_source(session, owner_id, identity):
    return SourceRegistry(PidsRepositories(session).sources).register(
        owner_id,
        SourceKind.MANUAL,
        f"Synthetic {identity}",
        identity,
        account_ref=f"synthetic:{identity}",
    )


def item(owner_id, source, text, external_id=None, **extra):
    return IncomingRecord(
        owner_id=owner_id,
        source_id=source.id,
        external_id=external_id,
        text=text,
        occurred_at=extra.pop("occurred_at", NOW),
        source_timezone=extra.pop("source_timezone", "Asia/Kolkata"),
        **extra,
    )


def test_source_registry_is_stable_owner_scoped_and_rejects_paused_sources(isolated_session):
    registry = SourceRegistry(PidsRepositories(isolated_session).sources)
    source = registry.register(301, SourceKind.MANUAL, "Inbox", "manual-inbox")
    duplicate = registry.register(301, SourceKind.MANUAL, "Changed label", "manual-inbox")
    assert duplicate.id == source.id
    assert duplicate.display_name == "Inbox"
    assert registry.register(302, SourceKind.MANUAL, "Inbox", "manual-inbox").id != source.id
    assert registry.get(301, source.id) == source
    with pytest.raises(PidsNotFoundError):
        registry.get(302, source.id)
    registry.set_status(301, source.id, SourceStatus.PAUSED)
    with pytest.raises(ValueError, match="not active"):
        _pipeline(isolated_session).process(301, registry.get(301, source.id), ())


def test_normalization_is_deterministic_bounded_and_preserves_input(isolated_session):
    source = register_source(isolated_session, 311, "normalize")
    original = "  cafe\u0301  \r\n\r\n  Submit report\tby Friday.  "
    incoming = item(311, source, original, "normalized-1", metadata={"b": 2, "a": 1})
    one = normalize_record(incoming)
    two = normalize_record(incoming)
    assert one == two
    assert one.text == "café\n\nSubmit report by Friday."
    assert incoming.text == original
    assert one.normalized_sha256 == content_sha256(one.text.encode("utf-8"))
    assert normalize_text("") == ""
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    with pytest.raises(ValueError, match="finite JSON"):
        canonical_json({"bad": float("nan")})
    with pytest.raises(ValueError, match="byte limit"):
        normalize_record(item(311, source, "ok", "metadata-limit", metadata={"large": "x" * 17000}))
    with pytest.raises(ValueError, match="UTF-8 byte limit"):
        item(311, source, "é" * 131073, "record-byte-limit")
    with pytest.raises(ValueError, match="null"):
        normalize_text("bad\x00text")


def test_understanding_extracts_reviewable_tasks_deadlines_events_and_entities():
    text = (
        "Submit the assignment by Friday.\n"
        "Prepare Project Apollo report by 2026-11-18.\n"
        "Meeting with Alex Morgan at Acme Inc on November 18, 2026.\n"
        "Alex Morgan works at Acme Inc.\n"
        "Project Apollo is blocked.\n"
        "Submit application by 03/04/2027.\n"
        "The report is due Friday.\n"
        "The report is not due until Monday."
    )
    result = understand(
        text, reference_time=NOW, source_timezone="Asia/Kolkata"
    )
    assert [candidate.fields["description"] for candidate in result.tasks] == [
        "Submit the assignment by Friday",
        "Prepare Project Apollo report by 2026-11-18",
        "Submit application by 03/04/2027",
    ]
    assert result.deadlines[0].fields == {
        "expression": "Friday",
        "due_date": "2026-11-13",
        "resolution": "resolved_relative",
        "deadline_signal": True,
    }
    meeting_date = next(
        candidate
        for candidate in result.deadlines
        if candidate.fields["expression"] == "November 18, 2026"
    )
    assert meeting_date.fields["deadline_signal"] is False
    negated_deadline = next(
        candidate
        for candidate in result.deadlines
        if candidate.fields["expression"] == "Monday"
    )
    assert negated_deadline.fields["deadline_signal"] is False
    assert any(
        candidate.fields["resolution"] == "ambiguous_numeric_date"
        and candidate.fields["due_date"] is None
        for candidate in result.deadlines
    )
    assert result.events
    assert {(item.entity_type, item.canonical_name) for item in result.entities} >= {
        ("person", "Alex Morgan"),
        ("organization", "Acme Inc"),
        ("project", "Apollo"),
    }
    assert result.relationships[0].fields["relation_type"] == "works_for"
    assert result.facts[0].fields == {"subject": "Apollo", "status": "blocked"}
    assert all(candidate.review_status == "needs_review" for candidate in result.tasks)
    assert not result.tasks[-1].fields.get("description", "").startswith("The report")


def test_relative_deadline_without_reference_timezone_remains_unresolved():
    result = understand("Call Alex Morgan tomorrow.", reference_time=NOW)
    assert result.deadlines[0].fields["due_date"] is None
    assert result.deadlines[0].fields["resolution"] == "missing_reference_timezone"
    with pytest.raises(ValueError, match="timezone"):
        understand("Call Alex Morgan tomorrow.", reference_time=datetime(2026, 11, 12, 4))
    with pytest.raises(ValueError, match="IANA timezone"):
        understand("Call Alex Morgan tomorrow.", reference_time=NOW, source_timezone="Mars/Olympus")


def test_synthetic_two_tenant_pipeline_is_idempotent_and_preserves_provenance(isolated_session):
    pipeline = _pipeline(isolated_session)
    owners = (401, 402)
    sources = {
        (owner, suffix): register_source(isolated_session, owner, f"source-{owner}-{suffix}")
        for owner in owners
        for suffix in ("a", "b")
    }
    corpus = {
        (401, "a"): [
            "Submit the assignment by Friday.",
            "Prepare Project Apollo report by 2026-11-18.",
            "Submit the assignment by 2026-11-20.",
            "Meeting with Alex Morgan at Acme Inc on November 18, 2026.",
            "Alex Morgan works at Acme Inc.",
            "Submit application by 03/04/2027.",
        ],
        (401, "b"): [
            "Project Apollo planning notes.",
            "Call Alex Morgan today.",
            "Submit the revised plan by 2026-11-20.",
            "Meet with Jamie Lee in Seattle.",
            "Reference record with no action.",
            "Schedule a review for next Friday.",
        ],
        (402, "a"): [
            "Submit the assignment by Friday.",
            "Prepare Project Apollo report by 2026-11-18.",
            "Call Alex Morgan tomorrow.",
            "Project Orion update.",
            "Meeting with Sam Taylor at Northwind LLC.",
            "The 03/04/2027 date is ambiguous.",
        ],
        (402, "b"): [
            "Contact Jamie Lee about the event.",
            "Review the project brief by 2026-11-19.",
            "Visit Boston for the interview.",
            "Respond to the notice tomorrow.",
            "Build the demonstration for Project Orion.",
            "Record with no actionable statement.",
        ],
    }
    original_texts = []
    incoming_records = []
    initial_results = {}
    for (owner_id, suffix), texts in corpus.items():
        source = sources[(owner_id, suffix)]
        records = []
        for index, text in enumerate(texts):
            external_id = f"{owner_id}-{suffix}-{index}"
            records.append(item(owner_id, source, text, external_id))
            original_texts.append(text)
            incoming_records.append(records[-1])
        initial_results[(owner_id, suffix)] = pipeline.process(owner_id, source, records)

    source = sources[(401, "a")]
    duplicate_content = item(401, source, corpus[(401, "a")][0], "401-a-content-duplicate")
    replay = item(401, source, corpus[(401, "a")][0], "401-a-0")
    invalid_owner = item(402, source, "Cross-owner input must fail.", "forged-owner")
    result = pipeline.process(401, source, [duplicate_content, replay, invalid_owner])

    assert result.counts["duplicate_content"] == 1
    assert result.counts["replayed"] == 1
    assert result.counts["failed"] == 1
    assert result.records[-1].error_code == "ownership_or_reference"
    assert sum(value.counts["new"] for value in initial_results.values()) == 24
    assert all(owner_id in owners for owner_id, _ in initial_results)
    assert [record.text for record in incoming_records] == original_texts
    assert isolated_session.scalar(select(func.count()).select_from(RawRecordRow)) == 25
    assert isolated_session.scalar(select(func.count()).select_from(KnowledgeRecordRow)) == 25
    assert isolated_session.scalar(select(func.count()).select_from(DeadlineRow)) >= 8
    assert isolated_session.scalar(select(func.count()).select_from(EntityRow)) > 5
    assert isolated_session.scalar(select(func.count()).select_from(RelationshipRow)) >= 1
    deadline_dates = {
        row.due_date
        for row in isolated_session.scalars(
            select(DeadlineRow).where(DeadlineRow.owner_id == 401)
        )
    }
    assert {"2026-11-13", "2026-11-20"} <= {
        value.isoformat() for value in deadline_dates
    }

    rows = isolated_session.scalars(select(RawRecordRow)).all()
    assert all(row.owner_id in owners for row in rows)
    owner_a_entities = PidsRepositories(isolated_session).entities.list_owned(401)
    owner_b_entities = PidsRepositories(isolated_session).entities.list_owned(402)
    assert {entity.owner_id for entity in owner_a_entities} == {401}
    assert {entity.owner_id for entity in owner_b_entities} == {402}
    assert {entity.id for entity in owner_a_entities}.isdisjoint(
        {entity.id for entity in owner_b_entities}
    )

    relationship_count = isolated_session.scalar(
        select(func.count()).select_from(RelationshipRow)
    )
    repeated = {}
    for (owner_id, suffix), texts in corpus.items():
        source = sources[(owner_id, suffix)]
        repeated[(owner_id, suffix)] = pipeline.process(
            owner_id,
            source,
            [item(owner_id, source, text, f"{owner_id}-{suffix}-{i}") for i, text in enumerate(texts)],
        )
    assert all(result.counts["replayed"] == 6 for result in repeated.values())
    assert all(result.counts["new"] == 0 for result in repeated.values())
    assert isolated_session.scalar(select(func.count()).select_from(RawRecordRow)) == 25
    assert isolated_session.scalar(select(func.count()).select_from(KnowledgeRecordRow)) == 25
    assert isolated_session.scalar(select(func.count()).select_from(DeadlineRow)) >= 8
    assert isolated_session.scalar(
        select(func.count()).select_from(RelationshipRow)
    ) == relationship_count
    first_knowledge_id = initial_results[(401, "a")].records[0].knowledge_record_id
    repeated_knowledge_id = repeated[(401, "a")].records[0].knowledge_record_id
    assert first_knowledge_id == repeated_knowledge_id


def test_email_and_url_identifiers_match_without_cross_owner_merging(isolated_session):
    pipeline = _pipeline(isolated_session)
    source = register_source(isolated_session, 411, "url-email")
    first = pipeline.process(
        411,
        source,
        [item(411, source, "Review https://Example.com/opportunity and write to Sam@example.com.", "id-1")],
    )
    replay_identity = pipeline.process(
        411,
        source,
        [item(411, source, "Review http://example.com/opportunity and write to sam@example.com.", "id-2")],
    )
    assert {match["match_type"] for match in first.records[0].entity_matches} == {"new"}
    assert {match["match_type"] for match in replay_identity.records[0].entity_matches} == {
        "exact"
    }
    assert len(PidsRepositories(isolated_session).entities.list_owned(411)) == 2
    assert not PidsRepositories(isolated_session).entities.list_owned(412)


def test_event_date_is_not_persisted_as_a_deadline(isolated_session):
    source = register_source(isolated_session, 416, "event-date")
    result = _pipeline(isolated_session).process(
        416,
        source,
        [item(416, source, "Meeting with Alex Morgan on 2026-11-18.", "meeting-date")],
    )
    assert result.records[0].deadline_count == 0
    knowledge = PidsRepositories(isolated_session).knowledge_records.get(
        416, result.records[0].knowledge_record_id
    )
    date_candidate = knowledge.data["deadlines"][0]
    assert date_candidate["fields"]["deadline_signal"] is False
    assert isolated_session.scalar(
        select(func.count()).select_from(DeadlineRow).where(DeadlineRow.owner_id == 416)
    ) == 0


def test_entity_resolution_is_explainable_and_possible_matches_are_not_merged(isolated_session):
    pipeline = _pipeline(isolated_session)
    source = register_source(isolated_session, 421, "entity-match")
    first = pipeline.process(
        421, source, [item(421, source, "Contact Alice Morgan.", "person-1")]
    )
    second = pipeline.process(
        421, source, [item(421, source, "Contact Alice Morgan.", "person-2")]
    )
    possible = pipeline.process(
        421, source, [item(421, source, "Contact Alice Morgan Jones.", "person-3")]
    )
    assert first.records[0].entity_matches[0]["match_type"] == "new"
    assert second.records[0].entity_matches[0]["match_type"] == "exact"
    possible_decision = next(
        result
        for result in possible.records[0].entity_matches
        if result["entity_type"] == "person"
    )
    assert possible_decision["match_type"] == "possible"
    assert possible.records[0].possible_entity_match_count == 1
    assert len(PidsRepositories(isolated_session).entities.list_owned(421)) == 2


def test_exact_alias_resolution_and_knowledge_review_linking(isolated_session):
    repositories = PidsRepositories(isolated_session)
    source = register_source(isolated_session, 431, "alias")
    pipeline_record = IncomingRecord(
        owner_id=431, source_id=source.id, external_id="seed", text="seed"
    )
    raw_result = _pipeline(isolated_session).process(431, source, [pipeline_record])
    raw_id = raw_result.records[0].raw_record_id
    evidence = SourceReference(
        owner_id=431, source_id=source.id, raw_record_id=raw_id, evidence_location="seed"
    )
    entity = Entity(
        id=uuid4(),
        owner_id=431,
        entity_type="person",
        canonical_name="Alexander Morgan",
        aliases=("Alex Morgan",),
        evidence=(evidence,),
        created_at=NOW,
    )
    repositories.entities.create(entity)
    result = _pipeline(isolated_session).process(
        431, source, [item(431, source, "Call Alex Morgan today.", "alias-lookup")]
    )
    assert result.records[0].entity_matches[0]["match_type"] == "strong"
    knowledge = repositories.knowledge_records.get(431, result.records[0].knowledge_record_id)
    assert knowledge.verification.value == "needs_review"


def test_batch_limit_and_per_record_error_isolation(isolated_session):
    source = register_source(isolated_session, 441, "bounded")
    pipeline = _pipeline(isolated_session, max_batch_size=2)
    result = pipeline.process(
        441,
        source,
        [
            item(441, source, "Review the first document.", "first"),
            item(999, source, "Wrong owner.", "wrong-owner"),
            item(441, source, "Review the third document.", "third"),
        ],
    )
    assert [record.status for record in result.records] == ["new", "failed", "failed"]
    assert result.records[1].error_code == "ownership_or_reference"
    assert result.records[2].error_code == "batch_limit"


def test_connector_run_reports_retryable_source_error_without_losing_received_records(isolated_session):
    source = register_source(isolated_session, 451, "connector")

    class SyntheticConnector:
        kind = SourceKind.MANUAL
        capabilities = frozenset({"read", "pagination"})

        def fetch(self, registered_source, *, cursor=None):
            assert cursor == "next-page"
            yield item(451, registered_source, "Submit the report by 2026-11-18.", "item-1")
            raise ConnectorError("temporary_source_failure", retryable=True)

    result = _pipeline(isolated_session).run(
        451, source.id, SyntheticConnector(), cursor="next-page"
    )
    assert result.counts["new"] == 1
    assert result.source_error_code == "temporary_source_failure"
    assert result.source_retryable is True


def test_failed_record_savepoint_rolls_back_only_that_record(isolated_session):
    source = register_source(isolated_session, 461, "savepoint")
    pipeline = _pipeline(isolated_session)
    result = pipeline.process(
        461,
        source,
        [
            item(461, source, "Submit the task by 2026-11-18.", "good"),
            item(461, source, "Different content with stable identity.", "good"),
        ],
    )
    assert [record.status for record in result.records] == ["new", "failed"]
    assert result.records[1].error_code == "source_identity_conflict"
    assert isolated_session.scalar(
        select(func.count()).select_from(RawRecordRow).where(RawRecordRow.owner_id == 461)
    ) == 1


def test_outer_rollback_removes_synthetic_pipeline_writes(isolated_session):
    source = register_source(isolated_session, 471, "rollback")
    raw_before = isolated_session.scalar(select(func.count()).select_from(RawRecordRow))
    result = _pipeline(isolated_session).process(
        471, source, [item(471, source, "Prepare a rollback test.", "rollback-1")]
    )
    assert result.counts["new"] == 1
    isolated_session.rollback()
    assert isolated_session.scalar(select(func.count()).select_from(RawRecordRow)) == 0
    assert raw_before > 0
