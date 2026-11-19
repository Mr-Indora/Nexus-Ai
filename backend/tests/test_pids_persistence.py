from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pids.contracts import (
    Deadline,
    Entity,
    Feedback,
    FeedbackAction,
    KnowledgeRecord,
    RawRecord,
    Relationship,
    Source,
    SourceKind,
    SourceReference,
    content_sha256,
)
from pids.persistence.database import (
    ISOLATED_WORKSPACE,
    create_pids_engine,
    create_session_factory,
    validate_isolated_database_url,
)
from pids.persistence.models import (
    Base,
    DeadlineEvidenceRow,
    EntityAliasRow,
    EntityEvidenceRow,
    KnowledgeEntityRow,
    KnowledgeRecordRow,
    KnowledgeSourceRow,
    RawRecordRow,
    RelationshipEvidenceRow,
    SourceRow,
)
from pids.persistence.repositories import (
    PidsConflictError,
    PidsDuplicateSourceConflict,
    PidsNotFoundError,
    PidsRepositories,
)


UTC = timezone.utc
NOW = datetime(2026, 11, 12, 4, 0, tzinfo=UTC)
OWNER_ID = 17
OTHER_OWNER_ID = 29
ALEMBIC_CONFIG = Path(__file__).resolve().parents[1] / "pids" / "persistence" / "alembic.ini"


def _database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


@pytest.fixture
def persistence():
    ISOLATED_WORKSPACE.mkdir(parents=True, exist_ok=True)
    path = ISOLATED_WORKSPACE / f"test-{uuid4().hex}.db"
    url = _database_url(path)
    config = Config(str(ALEMBIC_CONFIG))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    engine = create_pids_engine(url)
    session = create_session_factory(engine)()
    try:
        yield session, PidsRepositories(session), engine, path, config
    finally:
        session.rollback()
        session.close()
        engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            generated_file = Path(f"{path}{suffix}")
            if generated_file.exists():
                generated_file.unlink()


def _source(owner_id: int = OWNER_ID, source_id=None) -> Source:
    return Source(
        id=source_id or uuid4(),
        owner_id=owner_id,
        kind=SourceKind.MANUAL,
        display_name="Synthetic source",
        account_ref="synthetic-account",
        created_at=NOW,
        updated_at=NOW,
    )


def _reference(owner_id: int, source_id, raw_id, evidence_location="body:0-12"):
    return SourceReference(
        owner_id=owner_id,
        source_id=source_id,
        raw_record_id=raw_id,
        evidence_location=evidence_location,
    )


def _record(
    owner_id: int, source_id, raw_id, external_id="item-1", payload=b"synthetic",
    source_version=None,
):
    return RawRecord(
        id=raw_id,
        owner_id=owner_id,
        source_id=source_id,
        external_id=external_id,
        content_sha256=content_sha256(payload),
        ingested_at=NOW,
        source_version=source_version,
    )


def _knowledge(owner_id: int, raw, *, record_id=None, data=None, version="rules-v1"):
    return KnowledgeRecord(
        id=record_id or uuid4(),
        owner_id=owner_id,
        record_type="task_candidate",
        data=data if data is not None else {"title": "Synthetic task"},
        source_refs=(_reference(owner_id, raw.source_id, raw.id),),
        processing_version=version,
        created_at=NOW,
    )


def _prepare_source_and_raw(repositories, owner_id=OWNER_ID, source=None, external_id="item-1"):
    source = source or _source(owner_id)
    repositories.sources.create(source)
    record = _record(owner_id, source.id, uuid4(), external_id=external_id)
    persisted = repositories.raw_records.create_idempotent(record)
    return source, persisted


def _drop_engine_session(session: Session, engine) -> None:
    session.rollback()
    session.close()
    engine.dispose()


def test_isolated_base_and_engine_guard_never_accept_application_database():
    assert Base.metadata is not None
    app_db = Path(__file__).resolve().parents[1] / "nexus.db"
    with pytest.raises(ValueError, match="inside pids-isolated-work"):
        validate_isolated_database_url(_database_url(app_db))
    with pytest.raises(ValueError, match="absolute"):
        validate_isolated_database_url("sqlite:///relative.db")
    with pytest.raises(ValueError, match="in-memory"):
        validate_isolated_database_url("sqlite:///:memory:")
    with pytest.raises(ValueError, match="file-backed"):
        validate_isolated_database_url("postgresql://not-used")


def test_migration_schema_tables_indexes_foreign_keys_and_nullability(persistence):
    session, _, engine, _, _ = persistence
    inspector = inspect(engine)
    expected_tables = set(Base.metadata.tables) | {"pids_alembic_version"}
    assert set(inspector.get_table_names()) == expected_tables

    for table_name, model_table in Base.metadata.tables.items():
        db_columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        assert set(db_columns) == set(model_table.columns.keys())
        assert tuple(inspector.get_pk_constraint(table_name)["constrained_columns"]) == tuple(
            column.name for column in model_table.primary_key.columns
        )
        assert {
            column_name: details["nullable"]
            for column_name, details in db_columns.items()
        } == {column.name: column.nullable for column in model_table.columns}
        assert {
            index["name"] for index in inspector.get_indexes(table_name)
        } == {index.name for index in model_table.indexes}
        assert {
            constraint["name"] for constraint in inspector.get_unique_constraints(table_name)
        } == {
            constraint.name
            for constraint in model_table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        actual_foreign_keys = {
            (
                tuple(foreign_key["constrained_columns"]),
                foreign_key["referred_table"],
                tuple(foreign_key["referred_columns"]),
                foreign_key["options"].get("ondelete"),
                foreign_key["name"],
            )
            for foreign_key in inspector.get_foreign_keys(table_name)
        }
        expected_foreign_keys = {
            (
                tuple(constraint.column_keys),
                next(iter(constraint.elements)).column.table.name,
                tuple(element.column.name for element in constraint.elements),
                next(iter(constraint.elements)).ondelete,
                constraint.name,
            )
            for constraint in model_table.foreign_key_constraints
        }
        assert actual_foreign_keys == expected_foreign_keys
    assert inspector.get_pk_constraint("pids_sources")["constrained_columns"] == ["id"]
    assert inspector.get_unique_constraints("pids_raw_records")
    assert len(inspector.get_foreign_keys("pids_raw_records")) == 1
    assert session.scalar(text("PRAGMA foreign_keys")) == 1
    expected_revision = ScriptDirectory.from_config(Config(str(ALEMBIC_CONFIG))).get_current_head()
    assert session.scalar(text("SELECT version_num FROM pids_alembic_version")) == expected_revision
    with engine.connect() as connection_one, engine.connect() as connection_two:
        assert connection_one.scalar(text("PRAGMA foreign_keys")) == 1
        assert connection_two.scalar(text("PRAGMA foreign_keys")) == 1

    raw_columns = {column["name"]: column for column in inspector.get_columns("pids_raw_records")}
    assert raw_columns["external_id"]["nullable"] is False
    assert raw_columns["content_ref"]["nullable"] is True
    assert raw_columns["source_timezone"]["nullable"] is True
    assert raw_columns["occurred_at"]["nullable"] is True


def test_create_and_scoped_retrieve_all_domain_records_and_update_mutable_fields(persistence):
    session, repositories, _, _, _ = persistence
    source, raw = _prepare_source_and_raw(repositories)
    knowledge = repositories.knowledge_records.create_idempotent(_knowledge(OWNER_ID, raw))
    entity = Entity(
        id=uuid4(),
        owner_id=OWNER_ID,
        entity_type="person",
        canonical_name="Alex Example",
        aliases=("Alex",),
        evidence=(_reference(OWNER_ID, source.id, raw.id),),
        created_at=NOW,
    )
    entity = repositories.entities.create(entity)
    repositories.knowledge_records.link_entity(OWNER_ID, knowledge.id, entity.id)
    other_entity = repositories.entities.create(
        Entity(
            id=uuid4(),
            owner_id=OWNER_ID,
            entity_type="project",
            canonical_name="PIDS",
            evidence=(_reference(OWNER_ID, source.id, raw.id),),
            created_at=NOW,
        )
    )
    relationship = Relationship(
        id=uuid4(),
        owner_id=OWNER_ID,
        subject_id=entity.id,
        object_id=other_entity.id,
        relation_type="associated_with",
        evidence=(_reference(OWNER_ID, source.id, raw.id),),
        created_at=NOW,
    )
    deadline = Deadline(
        id=uuid4(),
        owner_id=OWNER_ID,
        record_id=knowledge.id,
        due_date=date(2026, 12, 1),
        evidence=(_reference(OWNER_ID, source.id, raw.id),),
        created_at=NOW,
    )
    feedback = Feedback(
        id=uuid4(),
        owner_id=OWNER_ID,
        target_type="task",
        target_id="legacy-task-42",
        action=FeedbackAction.CONFIRM,
        occurred_at=NOW,
    )

    assert repositories.sources.get(OWNER_ID, source.id) == source
    assert repositories.raw_records.get(OWNER_ID, raw.id) == raw
    assert repositories.knowledge_records.get(OWNER_ID, knowledge.id) == knowledge
    assert repositories.entities.create(entity) == entity
    assert repositories.entities.get(OWNER_ID, entity.id) == entity
    assert repositories.relationships.create(relationship) == relationship
    assert repositories.relationships.get(OWNER_ID, relationship.id) == relationship
    assert repositories.deadlines.create(deadline) == deadline
    assert repositories.deadlines.get(OWNER_ID, deadline.id) == deadline
    assert repositories.feedback.create(feedback) == feedback
    assert repositories.feedback.get(OWNER_ID, feedback.id) == feedback

    updated_source = repositories.sources.update(
        OWNER_ID, source.id, display_name="Renamed synthetic source"
    )
    assert updated_source.display_name == "Renamed synthetic source"
    with pytest.raises(ValueError, match="immutable"):
        repositories.knowledge_records.update(
            OWNER_ID, knowledge.id, verification="user_confirmed"
        )
    assert repositories.knowledge_records.get(OWNER_ID, knowledge.id) == knowledge
    updated_entity = repositories.entities.update(
        OWNER_ID, entity.id, aliases=("Alexander",)
    )
    assert updated_entity.aliases == ("Alexander",)
    updated_deadline = repositories.deadlines.update(
        OWNER_ID, deadline.id, due_date=date(2026, 12, 2)
    )
    assert updated_deadline.due_date == date(2026, 12, 2)
    session.commit()


def test_tenant_composite_foreign_keys_and_repository_checks_reject_cross_owner_links(persistence):
    session, repositories, _, _, _ = persistence
    source, raw = _prepare_source_and_raw(repositories)
    owner_record = _knowledge(OWNER_ID, raw)
    repositories.knowledge_records.create_idempotent(owner_record)

    with pytest.raises(PidsNotFoundError):
        repositories.raw_records.create_idempotent(
            _record(OTHER_OWNER_ID, source.id, uuid4(), external_id="other-owner")
        )
    with pytest.raises(PidsNotFoundError):
        repositories.knowledge_records.create_idempotent(
            _knowledge(OTHER_OWNER_ID, raw)
        )
    with pytest.raises(PidsNotFoundError):
        repositories.knowledge_records.get(OTHER_OWNER_ID, owner_record.id)
    with pytest.raises(PidsNotFoundError):
        repositories.sources.update(
            OTHER_OWNER_ID, source.id, display_name="Unauthorized change"
        )
    assert repositories.sources.get(OWNER_ID, source.id).display_name == source.display_name
    assert not hasattr(repositories.sources, "delete")

    bad_link = KnowledgeSourceRow(
        owner_id=OTHER_OWNER_ID,
        knowledge_id=str(owner_record.id),
        source_id=str(source.id),
        raw_record_id=str(raw.id),
    )
    session.add(bad_link)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_exact_source_idempotency_conflicts_duplicates_and_source_scoping(persistence):
    session, repositories, _, _, _ = persistence
    source_one, raw = _prepare_source_and_raw(repositories)
    replay = _record(OWNER_ID, source_one.id, uuid4(), payload=b"synthetic")
    same = repositories.raw_records.create_idempotent(replay)
    assert same.id == raw.id

    with pytest.raises(PidsDuplicateSourceConflict):
        repositories.raw_records.create_idempotent(
            _record(OWNER_ID, source_one.id, uuid4(), payload=b"changed content")
        )

    source_two = _source(OWNER_ID)
    repositories.sources.create(source_two)
    same_external_other_source = repositories.raw_records.create_idempotent(
        _record(OWNER_ID, source_two.id, uuid4(), payload=b"synthetic")
    )
    assert same_external_other_source.source_id == source_two.id

    source_other_owner = _source(OTHER_OWNER_ID)
    repositories.sources.create(source_other_owner)
    same_external_other_owner = repositories.raw_records.create_idempotent(
        _record(OTHER_OWNER_ID, source_other_owner.id, uuid4(), payload=b"synthetic")
    )
    assert same_external_other_owner.external_id == raw.external_id
    expected_candidates = tuple(
        sorted(
            (raw, same_external_other_source),
            key=lambda item: (str(item.source_id), item.external_id),
        )
    )
    assert repositories.raw_records.find_content_candidates(
        OWNER_ID, raw.content_sha256
    ) == expected_candidates
    session.commit()


def test_changed_source_version_is_preserved_as_a_distinct_raw_record(persistence):
    _, repositories, _, _, _ = persistence
    source, first = _prepare_source_and_raw(repositories)
    revised = repositories.raw_records.create_idempotent(
        _record(
            OWNER_ID,
            source.id,
            uuid4(),
            payload=b"synthetic revised body",
            source_version="gmail-payload-v2",
        )
    )
    assert revised.id != first.id
    assert revised.external_id == first.external_id
    assert revised.source_version == "gmail-payload-v2"
    assert repositories.raw_records.get(OWNER_ID, first.id).content_sha256 != revised.content_sha256


def test_knowledge_identity_is_idempotent_and_conflicting_payload_is_rejected(persistence):
    _, repositories, _, _, _ = persistence
    _, raw = _prepare_source_and_raw(repositories)
    original = _knowledge(OWNER_ID, raw)
    stored = repositories.knowledge_records.create_idempotent(original)
    replay = _knowledge(OWNER_ID, raw, data=original.data)
    replayed = repositories.knowledge_records.create_idempotent(replay)
    assert replayed.id == stored.id

    with pytest.raises(PidsConflictError, match="different derived content"):
        repositories.knowledge_records.create_idempotent(
            _knowledge(OWNER_ID, raw, data={"title": "Conflicting output"})
        )


def test_concurrent_duplicate_knowledge_creation_is_idempotent(persistence):
    session, repositories, engine, _, _ = persistence
    _, raw = _prepare_source_and_raw(repositories)
    session.commit()
    barrier = Barrier(2)
    record_ids = [uuid4(), uuid4()]

    def create(record_id):
        local_session = create_session_factory(engine)()
        try:
            local_repository = PidsRepositories(local_session)
            record = _knowledge(OWNER_ID, raw, record_id=record_id)
            barrier.wait(timeout=5)
            persisted = local_repository.knowledge_records.create_idempotent(record)
            local_session.commit()
            return persisted.id
        finally:
            local_session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        persisted_ids = list(executor.map(create, record_ids))
    assert persisted_ids[0] == persisted_ids[1]
    assert session.scalar(select(KnowledgeRecordRow.idempotency_key)) is not None
    assert len(session.scalars(select(KnowledgeRecordRow)).all()) == 1


def test_utc_datetime_mapping_normalizes_non_utc_database_round_trip(persistence):
    session, repositories, _, _, _ = persistence
    source, _ = _prepare_source_and_raw(repositories)
    local_time = datetime(2026, 11, 12, 9, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    raw_id = uuid4()
    raw_row = RawRecordRow(
        id=str(raw_id),
        owner_id=OWNER_ID,
        source_id=str(source.id),
        external_id="offset-time",
        content_sha256=content_sha256(b"offset-time"),
        occurred_at=local_time,
        ingested_at=local_time,
        created_at=local_time,
        updated_at=local_time,
    )
    session.add(raw_row)
    session.flush()

    persisted = session.get(RawRecordRow, str(raw_id))
    assert persisted.occurred_at == datetime(2026, 11, 12, 4, 0, tzinfo=UTC)
    assert persisted.ingested_at == datetime(2026, 11, 12, 4, 0, tzinfo=UTC)
    assert repositories.raw_records.get(OWNER_ID, raw_id).ingested_at == persisted.ingested_at


def test_concurrent_duplicate_source_ingestion_is_idempotent(persistence):
    session, repositories, engine, _, _ = persistence
    source, _ = _prepare_source_and_raw(repositories, external_id="seed")
    session.commit()
    barrier = Barrier(2)
    record_identities = [uuid4(), uuid4()]

    def ingest(record_id):
        local_session = create_session_factory(engine)()
        try:
            local_repository = PidsRepositories(local_session)
            record = _record(OWNER_ID, source.id, record_id, external_id="concurrent")
            barrier.wait(timeout=5)
            result = local_repository.raw_records.create_idempotent(record)
            local_session.commit()
            return result.id
        finally:
            local_session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(ingest, record_identities))
    assert results[0] == results[1]


def test_invalid_foreign_reference_and_rollback_preserve_consistency(persistence):
    session, repositories, _, _, _ = persistence
    invalid = RawRecord(
        id=uuid4(),
        owner_id=OWNER_ID,
        source_id=uuid4(),
        external_id="orphan",
        content_sha256=content_sha256(b"orphan"),
        ingested_at=NOW,
    )
    with pytest.raises(PidsNotFoundError):
        repositories.raw_records.create_idempotent(invalid)

    orphan_row = RawRecordRow(
        id=str(uuid4()),
        owner_id=OWNER_ID,
        source_id=str(uuid4()),
        external_id="orphan",
        content_sha256=content_sha256(b"orphan"),
        ingested_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(orphan_row)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    assert session.scalar(select(RawRecordRow)) is None


def test_link_cascades_are_local_and_source_deletion_is_restricted(persistence):
    session, repositories, _, _, _ = persistence
    source, raw = _prepare_source_and_raw(repositories)
    knowledge = repositories.knowledge_records.create_idempotent(_knowledge(OWNER_ID, raw))
    entity = repositories.entities.create(
        Entity(
            id=uuid4(),
            owner_id=OWNER_ID,
            entity_type="person",
            canonical_name="Evidence-backed person",
            aliases=("Person",),
            evidence=(_reference(OWNER_ID, source.id, raw.id),),
            created_at=NOW,
        )
    )
    repositories.knowledge_records.link_entity(OWNER_ID, knowledge.id, entity.id)
    session.commit()

    session.delete(session.get(KnowledgeRecordRow, str(knowledge.id)))
    session.commit()
    assert session.scalar(select(KnowledgeSourceRow)) is None
    assert session.scalar(select(KnowledgeEntityRow)) is None
    assert session.scalar(select(EntityAliasRow)) is not None
    assert session.scalar(select(EntityEvidenceRow)) is not None

    session.delete(session.get(SourceRow, str(source.id)))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    assert repositories.sources.get(OWNER_ID, source.id).id == source.id


def test_migration_can_repeat_downgrade_and_reupgrade_only_on_disposable_files(persistence):
    session, _, engine, first_path, config = persistence
    assert inspect(engine).has_table("pids_sources")
    session.close()
    engine.dispose()

    command.upgrade(config, "head")
    first_engine = create_pids_engine(_database_url(first_path))
    assert inspect(first_engine).has_table("pids_feedback")
    first_engine.dispose()

    second_path = ISOLATED_WORKSPACE / f"repeat-{uuid4().hex}.db"
    second_url = _database_url(second_path)
    second_config = Config(str(ALEMBIC_CONFIG))
    second_config.set_main_option("sqlalchemy.url", second_url)
    command.upgrade(second_config, "head")
    second_engine = create_pids_engine(second_url)
    assert set(inspect(second_engine).get_table_names()) == (
        set(Base.metadata.tables) | {"pids_alembic_version"}
    )
    second_engine.dispose()

    command.downgrade(config, "base")
    command.upgrade(config, "head")
    verify_engine = create_pids_engine(_database_url(first_path))
    assert set(inspect(verify_engine).get_table_names()) == (
        set(Base.metadata.tables) | {"pids_alembic_version"}
    )
    verify_engine.dispose()

    for suffix in ("", "-wal", "-shm"):
        generated_file = Path(f"{second_path}{suffix}")
        if generated_file.exists():
            generated_file.unlink()


def test_database_url_is_explicit_and_never_derived_from_application_environment(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///forbidden-default.db")
    path = ISOLATED_WORKSPACE / f"explicit-{uuid4().hex}.db"
    engine = create_pids_engine(_database_url(path))
    try:
        assert Path(engine.url.database).resolve() == path.resolve()
    finally:
        engine.dispose()
