from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from pids.contracts import (
    EvidenceReference,
    KnowledgeLifecycleState,
    KnowledgeRecord,
    RawRecord,
    SourceKind,
    SourceReference,
    content_sha256,
)
from pids.lifecycle import KnowledgeLifecycleService
from pids.normalization import IncomingRecord
from pids.persistence.database import (
    ISOLATED_WORKSPACE,
    create_pids_engine,
    create_session_factory,
)
from pids.persistence.repositories import (
    PidsConflictError,
    PidsNotFoundError,
    PidsRepositories,
)
from pids.pipeline import IntelligencePipeline
from pids.sources import SourceRegistry
from pids.understanding import RULE_VERSION
from pids.vault import MAX_PAYLOAD_BYTES, RawDataVault, VaultIntegrityError, VaultReport, VaultError


UTC = timezone.utc
NOW = datetime(2026, 11, 12, 4, 0, tzinfo=UTC)
ALEMBIC_CONFIG = Path(__file__).resolve().parents[1] / "pids" / "persistence" / "alembic.ini"


@pytest.fixture(scope="module")
def isolated_store():
    ISOLATED_WORKSPACE.mkdir(parents=True, exist_ok=True)
    database_path = ISOLATED_WORKSPACE / f"test-vault-life-{uuid4().hex}.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config(str(ALEMBIC_CONFIG))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_pids_engine(database_url)
    session = create_session_factory(engine)()
    try:
        yield session, PidsRepositories(session)
    finally:
        session.rollback()
        session.close()
        engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{database_path}{suffix}")
            if path.exists():
                path.unlink()
        

@pytest.fixture
def vault(isolated_store):
    root = ISOLATED_WORKSPACE / f"test-vault-blobs-{uuid4().hex}"
    instance = RawDataVault(root)
    yield instance
    import shutil

    if root.exists():
        shutil.rmtree(root)


@pytest.fixture(autouse=True)
def isolate_transaction(isolated_store):
    session = isolated_store[0]
    session.rollback()
    yield
    session.rollback()


def _source(repositories, owner_id):
    return SourceRegistry(repositories.sources).register(
        owner_id,
        SourceKind.MANUAL,
        "Synthetic input",
        f"owner-{owner_id}-source",
        account_ref=f"synthetic:{owner_id}",
    )


def _raw(repositories, vault, owner_id, source, payload=b"Submit by Friday.", external_id=None, **kwargs):
    digest, content_ref, size = vault.store(owner_id, payload)
    record = RawRecord(
        id=uuid4(),
        owner_id=owner_id,
        source_id=source.id,
        external_id=external_id or f"record-{uuid4()}",
        content_sha256=digest,
        content_ref=content_ref,
        payload_size_bytes=size,
        content_type=kwargs.get("content_type", "text/plain"),
        character_encoding=kwargs.get("character_encoding", "utf-8"),
        retention_expires_at=kwargs.get("retention_expires_at"),
        ingested_at=NOW,
    )
    return repositories.raw_records.create_idempotent(record)


def _knowledge(repositories, owner_id, source, raw, *, version=RULE_VERSION, data=None):
    record = KnowledgeRecord(
        id=uuid4(),
        owner_id=owner_id,
        record_type="fact_candidate",
        data=data or {"statement": "Synthetic statement"},
        source_refs=(
            SourceReference(
                owner_id=owner_id,
                source_id=source.id,
                raw_record_id=raw.id,
            ),
        ),
        processing_version=version,
        created_at=NOW,
    )
    return repositories.knowledge_records.create_idempotent(record)


def _pipeline_record(owner_id, source, text, external_id, **kwargs):
    return IncomingRecord(
        owner_id=owner_id,
        source_id=source.id,
        text=text,
        external_id=external_id,
        occurred_at=NOW,
        source_timezone="UTC",
        **kwargs,
    )


def test_vault_preserves_bytes_is_idempotent_and_enforces_size(isolated_store, vault):
    payload = b"\xfforiginal\r\nbytes\x00"
    digest, reference, size = vault.store(501, payload)
    assert digest == content_sha256(payload)
    assert reference == vault.reference(501, digest)
    assert size == len(payload)
    assert vault.read(501, digest) == payload
    assert vault.store(501, payload) == (digest, reference, size)
    assert vault.store(501, b"") == (
        content_sha256(b""),
        vault.reference(501, content_sha256(b"")),
        0,
    )
    with pytest.raises(ValueError, match="byte limit"):
        vault.store(501, b"x" * (MAX_PAYLOAD_BYTES + 1))


def test_vault_rejects_external_paths_and_digest_path_traversal(isolated_store, vault, tmp_path):
    with pytest.raises(ValueError, match="inside pids-isolated-work"):
        RawDataVault(tmp_path / "outside")
    with pytest.raises(ValueError, match="inside pids-isolated-work"):
        RawDataVault(ISOLATED_WORKSPACE / ".." / "outside")
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        vault.read(501, "../outside")


def test_vault_concurrent_duplicate_writes_and_integrity_failures(isolated_store, vault):
    payload = b"concurrent synthetic payload"
    with ThreadPoolExecutor(max_workers=4) as executor:
        stored = list(executor.map(lambda _: vault.store(502, payload), range(4)))
    assert len(set(stored)) == 1
    digest, _, _ = stored[0]
    path = vault._path(502, digest)
    path.write_bytes(b"corrupted")
    with pytest.raises(VaultIntegrityError, match="integrity"):
        vault.read(502, digest)
    path.unlink()
    with pytest.raises(VaultIntegrityError, match="missing"):
        vault.read(502, digest)


def test_vault_failed_atomic_replace_leaves_no_temporary_file(isolated_store, vault, monkeypatch):

    def fail_replace(*_args):
        raise OSError("synthetic filesystem failure")

    monkeypatch.setattr("pids.vault.os.replace", fail_replace)
    with pytest.raises(VaultError, match="could not be stored"):
        vault.store(503, b"must not be partially visible", encrypt=False)
    report = vault.reconcile(set())
    assert report.orphaned_refs == ()
    assert report.temporary_files == ()


def test_vault_reconciliation_is_dry_run_and_respects_shared_references(isolated_store, vault):
    digest, reference, _ = vault.store(504, b"shared synthetic blob")
    assert vault.reconcile({(504, digest)}).orphaned_refs == ()
    dry_run = vault.cleanup_orphans(set())
    assert dry_run.orphaned_refs == (reference,)
    assert dry_run.removed_refs == ()
    assert vault.read(504, digest) == b"shared synthetic blob"
    removed = vault.cleanup_orphans(set(), dry_run=False, minimum_age=timedelta(0))
    assert removed.removed_refs == (reference,)
    with pytest.raises(VaultIntegrityError, match="missing"):
        vault.read(504, digest)


def test_vault_rejects_symlink_root_when_supported(isolated_store, vault):
    link = ISOLATED_WORKSPACE / f"vault-link-{uuid4().hex}"
    try:
        link.symlink_to(vault.root, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is not permitted on this host")
    try:
        with pytest.raises(VaultIntegrityError, match="symbolic link|reparse point"):
            RawDataVault(link)
    finally:
        link.unlink(missing_ok=True)


def test_pipeline_persists_raw_bytes_candidates_and_idempotent_evidence(isolated_store, vault):
    session, repositories = isolated_store
    source = _source(repositories, 511)
    payload = "Submit report by Friday.".encode("utf-8")
    record = _pipeline_record(
        511,
        source,
        payload.decode("utf-8"),
        "raw-byte-test",
        payload_bytes=payload,
        content_type="text/plain; charset=utf-8",
    )
    pipeline = IntelligencePipeline(session, vault=vault)
    first = pipeline.process(511, source, [record])
    assert first.records[0].status == "new"
    raw = repositories.raw_records.get(511, first.records[0].raw_record_id)
    assert vault.read(511, raw.content_sha256) == payload
    assert raw.content_ref == vault.reference(511, raw.content_sha256)
    assert raw.payload_size_bytes == len(payload)
    knowledge = repositories.knowledge_records.get(511, first.records[0].knowledge_record_id)
    assert knowledge.lifecycle_status == KnowledgeLifecycleState.CANDIDATE
    assert first.records[0].evidence_count > 0
    evidence = repositories.evidence.list_for_knowledge(511, knowledge.id)
    assert len(evidence) == first.records[0].evidence_count
    assert all(
        repositories.evidence.validate(511, item.id, vault) == item
        and item.payload_sha256 == raw.content_sha256
        and item.offset_basis == "normalized_text_unicode_codepoints"
        for item in evidence
    )
    replay = pipeline.process(511, source, [record])
    assert replay.records[0].status == "replay"
    assert replay.records[0].knowledge_record_id == knowledge.id
    assert len(repositories.evidence.list_for_knowledge(511, knowledge.id)) == len(evidence)


def test_database_rollback_leaves_recoverable_encrypted_orphan(isolated_store, vault):
    session, repositories = isolated_store
    source = _source(repositories, 519)
    record = _pipeline_record(519, source, "Synthetic rollback record", "rollback-record")
    result = IntelligencePipeline(session, vault=vault).process(519, source, [record])
    assert result.records[0].status == "new"
    raw_id = result.records[0].raw_record_id
    raw = repositories.raw_records.get(519, raw_id)
    stored_path = vault._path(519, raw.content_sha256)
    assert stored_path.read_bytes().startswith(b"PIDSENC1")

    session.rollback()
    assert repositories.raw_records.blob_references() == set()
    cleanup = vault.cleanup_orphans(set(), dry_run=False)
    assert vault.reference(519, raw.content_sha256) in cleanup.orphaned_refs
    assert cleanup.removed_refs == ()
    assert vault.read(519, raw.content_sha256) == b"Synthetic rollback record"


def test_evidence_rejects_hash_offset_owner_and_missing_blob(isolated_store, vault):
    session, repositories = isolated_store
    source = _source(repositories, 521)
    raw = _raw(repositories, vault, 521, source, b"valid source text")
    knowledge = _knowledge(repositories, 521, source, raw)
    from pids.normalization import normalize_text

    normalized = normalize_text(vault.read(521, raw.content_sha256).decode("utf-8"))
    base = dict(
        id=uuid4(),
        owner_id=521,
        knowledge_id=knowledge.id,
        source_id=source.id,
        raw_record_id=raw.id,
        payload_sha256=raw.content_sha256,
        evidence_type="text",
        offset_basis="normalized_text_unicode_codepoints",
        start_offset=0,
        end_offset=len(normalized),
        extraction_method="synthetic_rule",
        extraction_version="test-v1",
        processed_at=NOW,
        transformation={
            "normalized_sha256": content_sha256(normalized.encode("utf-8"))
        },
        idempotency_key="a" * 64,
    )
    evidence = EvidenceReference(**base)
    repositories.evidence.create_idempotent(evidence, vault)
    assert repositories.evidence.validate(521, evidence.id, vault) == evidence
    with pytest.raises(ValueError, match="offsets exceed"):
        repositories.evidence.create_idempotent(
            EvidenceReference(
                **{
                    **base,
                    "id": uuid4(),
                    "end_offset": len(normalized) + 1,
                    "idempotency_key": "b" * 64,
                }
            ),
            vault,
        )
    with pytest.raises(PidsNotFoundError):
        repositories.evidence.list_for_knowledge(522, knowledge.id)
    vault._path(521, raw.content_sha256).unlink()
    with pytest.raises(VaultIntegrityError, match="missing"):
        repositories.evidence.validate(521, evidence.id, vault)


def test_structured_evidence_requires_a_real_json_pointer(isolated_store, vault):
    _, repositories = isolated_store
    source = _source(repositories, 531)
    raw = _raw(
        repositories,
        vault,
        531,
        source,
        b'{"task":{"title":"Submit"}}',
        content_type="application/json",
    )
    knowledge = _knowledge(repositories, 531, source, raw)
    valid = EvidenceReference(
        id=uuid4(),
        owner_id=531,
        knowledge_id=knowledge.id,
        source_id=source.id,
        raw_record_id=raw.id,
        payload_sha256=raw.content_sha256,
        evidence_type="structured",
        field_path="/task/title",
        extraction_method="synthetic_parser",
        extraction_version="json-v1",
        processed_at=NOW,
        idempotency_key="c" * 64,
    )
    repositories.evidence.create_idempotent(valid, vault)
    invalid = valid.model_copy(
        update={"id": uuid4(), "field_path": "/task/missing", "idempotency_key": "d" * 64}
    )
    with pytest.raises(ValueError, match="field path"):
        repositories.evidence.create_idempotent(invalid, vault)


def test_lifecycle_requires_review_is_owner_scoped_versioned_and_auditable(isolated_store, vault):
    session, repositories = isolated_store
    source = _source(repositories, 541)
    raw = _raw(repositories, vault, 541, source)
    knowledge = _knowledge(repositories, 541, source, raw)
    service = KnowledgeLifecycleService(repositories.knowledge_lifecycle)
    reviewer = SimpleNamespace(id=541)

    with pytest.raises(ValueError, match="authenticated principal"):
        service.transition(
            None,
            knowledge.id,
            expected_version=1,
            to_state=KnowledgeLifecycleState.REVIEWED,
            decision="review",
            request_id="missing-principal",
        )
    with pytest.raises(PidsNotFoundError):
        service.transition(
            SimpleNamespace(id=542),
            knowledge.id,
            expected_version=1,
            to_state=KnowledgeLifecycleState.REVIEWED,
            decision="review",
            request_id="other-owner",
        )
    with pytest.raises(PidsConflictError, match="not allowed"):
        service.transition(
            reviewer,
            knowledge.id,
            expected_version=1,
            to_state=KnowledgeLifecycleState.CONFIRMED,
            decision="confirm",
            request_id="skip-review",
        )

    reviewed, first_event = service.transition(
        reviewer,
        knowledge.id,
        expected_version=1,
        to_state=KnowledgeLifecycleState.REVIEWED,
        decision="checked-evidence",
        request_id="review-1",
    )
    assert reviewed.lifecycle_status == KnowledgeLifecycleState.REVIEWED
    assert reviewed.version == 2
    replayed, replay_event = service.transition(
        reviewer,
        knowledge.id,
        expected_version=1,
        to_state=KnowledgeLifecycleState.REVIEWED,
        decision="checked-evidence",
        request_id="review-1",
    )
    assert replayed.version == 2 and replay_event == first_event
    with pytest.raises(PidsConflictError, match="stale"):
        service.transition(
            reviewer,
            knowledge.id,
            expected_version=1,
            to_state=KnowledgeLifecycleState.REJECTED,
            decision="reject",
            request_id="stale-review",
            rejection_reason="stale request",
        )
    confirmed, _ = service.transition(
        reviewer,
        knowledge.id,
        expected_version=2,
        to_state=KnowledgeLifecycleState.CONFIRMED,
        decision="approved",
        request_id="confirm-1",
    )
    assert confirmed.lifecycle_status == KnowledgeLifecycleState.CONFIRMED
    assert confirmed.verification.value == "user_confirmed"
    assert len(repositories.knowledge_lifecycle.list_events(541, knowledge.id)) == 2
    with pytest.raises(ValueError, match="immutable"):
        repositories.knowledge_records.update(541, knowledge.id, data={"changed": True})


def test_lifecycle_rejection_and_outer_rollback_preserve_atomic_audit(isolated_store, vault):
    session, repositories = isolated_store
    source = _source(repositories, 551)
    raw = _raw(repositories, vault, 551, source)
    knowledge = _knowledge(repositories, 551, source, raw)
    session.commit()
    service = KnowledgeLifecycleService(repositories.knowledge_lifecycle)
    reviewer = SimpleNamespace(id=551)
    with pytest.raises(ValueError, match="requires a reason"):
        service.transition(
            reviewer,
            knowledge.id,
            expected_version=1,
            to_state=KnowledgeLifecycleState.REJECTED,
            decision="reject",
            request_id="missing-reason",
        )
    rejected, _ = service.transition(
        reviewer,
        knowledge.id,
        expected_version=1,
        to_state=KnowledgeLifecycleState.REJECTED,
        decision="incorrect",
        request_id="reject-1",
        rejection_reason="Evidence does not support candidate.",
    )
    assert rejected.lifecycle_status == KnowledgeLifecycleState.REJECTED
    assert rejected.rejection_reason
    session.rollback()
    persisted = repositories.knowledge_records.get(551, knowledge.id)
    assert persisted.lifecycle_status == KnowledgeLifecycleState.CANDIDATE
    assert repositories.knowledge_lifecycle.list_events(551, knowledge.id) == ()


def test_lifecycle_rejects_invalid_audit_fields_before_writing(isolated_store, vault):
    session, repositories = isolated_store
    source = _source(repositories, 556)
    raw = _raw(repositories, vault, 556, source)
    knowledge = _knowledge(repositories, 556, source, raw)
    service = KnowledgeLifecycleService(repositories.knowledge_lifecycle)

    with pytest.raises(ValueError, match="decision"):
        service.transition(
            SimpleNamespace(id=556),
            knowledge.id,
            expected_version=1,
            to_state=KnowledgeLifecycleState.REVIEWED,
            decision="x" * 65,
            request_id="invalid-decision",
        )
    with pytest.raises(ValueError, match="review request_id"):
        service.transition(
            SimpleNamespace(id=556),
            knowledge.id,
            expected_version=1,
            to_state=KnowledgeLifecycleState.REVIEWED,
            decision="review",
            request_id=" " * 129,
        )
    with pytest.raises(ValueError):
        service.record_conflict(
            SimpleNamespace(id=556),
            uuid4(),
            uuid4(),
            conflict_type="x" * 65,
            description="synthetic conflict",
        )

    unchanged = repositories.knowledge_records.get(556, knowledge.id)
    assert unchanged.lifecycle_status == KnowledgeLifecycleState.CANDIDATE
    assert unchanged.version == 1
    assert repositories.knowledge_lifecycle.list_events(556, knowledge.id) == ()


def test_conflicts_are_explicit_and_supersession_preserves_audit(isolated_store, vault):
    _, repositories = isolated_store
    source = _source(repositories, 561)
    old_raw = _raw(repositories, vault, 561, source, b"deadline Friday")
    old = _knowledge(repositories, 561, source, old_raw, data={"deadline": "Friday"})
    new_raw = _raw(repositories, vault, 561, source, b"deadline Monday")
    candidate = _knowledge(
        repositories, 561, source, new_raw, version="rules-v2", data={"deadline": "Monday"}
    )
    service = KnowledgeLifecycleService(repositories.knowledge_lifecycle)
    reviewer = SimpleNamespace(id=561)
    old, _ = service.transition(
        reviewer, old.id, expected_version=1, to_state="reviewed",
        decision="checked", request_id="old-review",
    )
    old, _ = service.transition(
        reviewer, old.id, expected_version=2, to_state="confirmed",
        decision="approved", request_id="old-confirm",
    )
    conflict = service.record_conflict(
        reviewer, old.id, candidate.id,
        conflict_type="contradictory_deadline",
        description="A newer candidate has different deadline evidence.",
    )
    assert conflict.status == "open"
    candidate, _ = service.transition(
        reviewer, candidate.id, expected_version=1, to_state="reviewed",
        decision="checked", request_id="new-review",
    )
    candidate, _ = service.transition(
        reviewer, candidate.id, expected_version=2, to_state="confirmed",
        decision="approved", request_id="new-confirm",
    )
    superseded, _ = service.transition(
        reviewer, old.id, expected_version=3, to_state="superseded",
        decision="explicit-supersession", request_id="supersede-old",
        superseded_by_id=candidate.id,
    )
    assert superseded.lifecycle_status == KnowledgeLifecycleState.SUPERSEDED
    assert superseded.superseded_by_id == candidate.id
    assert repositories.knowledge_lifecycle.list_events(561, old.id)[-1].to_state == (
        KnowledgeLifecycleState.SUPERSEDED
    )
