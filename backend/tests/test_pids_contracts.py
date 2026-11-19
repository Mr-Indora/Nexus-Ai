from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

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
    idempotency_key,
    same_content_candidate,
    same_source_item,
)


OWNER_ID = 7
SOURCE_ID = UUID("5cc05be6-7557-45e8-a4bb-3d744d0ad264")
RAW_ID = UUID("025b7734-49a7-4e16-b9ea-596f64b43fb3")
UTC = timezone.utc
NOW = datetime(2026, 11, 12, 4, 0, tzinfo=UTC)
HASH = content_sha256(b"synthetic source")


def source_reference(**overrides):
    fields = {
        "owner_id": OWNER_ID,
        "source_id": SOURCE_ID,
        "raw_record_id": RAW_ID,
    }
    fields.update(overrides)
    return SourceReference(**fields)


def raw_record(**overrides):
    fields = {
        "id": RAW_ID,
        "owner_id": OWNER_ID,
        "source_id": SOURCE_ID,
        "external_id": "provider-item-1",
        "content_sha256": HASH,
        "ingested_at": NOW,
    }
    fields.update(overrides)
    return RawRecord(**fields)


def test_source_and_raw_record_validate_and_normalize():
    source = Source(
        id=SOURCE_ID,
        owner_id=OWNER_ID,
        kind=SourceKind.GMAIL,
        display_name=" Inbox ",
        account_ref="account-7",
        created_at=NOW,
        updated_at=NOW + timedelta(minutes=1),
    )
    record = raw_record(
        external_id="  cafe\u0301-1  ",
        content_sha256=HASH.upper(),
        occurred_at=datetime(2026, 11, 12, 9, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))),
        source_timezone="Asia/Kolkata",
    )

    assert source.display_name == "Inbox"
    assert record.external_id == "café-1"
    assert record.content_sha256 == HASH
    assert record.occurred_at == datetime(2026, 11, 12, 4, 0, tzinfo=UTC)
    assert record.model_config["frozen"] is True


@pytest.mark.parametrize("owner_id", [0, -1, True, "7"])
def test_owner_id_must_be_a_positive_strict_integer(owner_id):
    with pytest.raises(ValidationError):
        raw_record(owner_id=owner_id)


@pytest.mark.parametrize("external_id", ["", "  "])
def test_external_identity_must_not_be_empty(external_id):
    with pytest.raises(ValidationError):
        raw_record(external_id=external_id)


def test_missing_or_malformed_record_identifier_is_rejected():
    fields = {
        "owner_id": OWNER_ID,
        "source_id": SOURCE_ID,
        "external_id": "provider-item-1",
        "content_sha256": HASH,
        "ingested_at": NOW,
    }
    with pytest.raises(ValidationError):
        RawRecord(**fields)
    with pytest.raises(ValidationError):
        raw_record(id="not-a-uuid")


def test_hash_and_timezone_are_validated():
    with pytest.raises(ValidationError):
        raw_record(content_sha256="not-a-hash")
    with pytest.raises(ValidationError):
        raw_record(source_timezone="Mars/Olympus")


def test_naive_timestamp_is_rejected_and_aware_timestamp_is_normalized():
    with pytest.raises(ValidationError, match="timezone"):
        raw_record(ingested_at=datetime(2026, 11, 12, 4, 0))
    assert raw_record(ingested_at=NOW).ingested_at.tzinfo == UTC


def test_idempotency_key_is_stable_scoped_and_unambiguous():
    first = idempotency_key(OWNER_ID, SOURCE_ID, "  cafe\u0301-1 ")
    assert first == idempotency_key(OWNER_ID, SOURCE_ID, "café-1")
    assert first != idempotency_key(OWNER_ID + 1, SOURCE_ID, "café-1")
    assert first != idempotency_key(OWNER_ID, uuid4(), "café-1")
    assert first != idempotency_key(OWNER_ID, SOURCE_ID, "café-2")
    with pytest.raises(ValueError):
        idempotency_key(True, SOURCE_ID, "email-1")


def test_exact_duplicate_identity_is_separate_from_content_candidate():
    original = raw_record()
    same_item = raw_record(id=uuid4())
    same_content_other_item = raw_record(id=uuid4(), external_id="provider-item-2")
    other_owner = raw_record(id=uuid4(), owner_id=OWNER_ID + 1)

    assert same_source_item(original, same_item)
    assert not same_source_item(original, same_content_other_item)
    assert not same_source_item(original, other_owner)
    assert same_content_candidate(original, same_content_other_item)
    assert not same_content_candidate(original, other_owner)


def test_knowledge_requires_owner_matched_source_provenance():
    record = KnowledgeRecord(
        id=uuid4(),
        owner_id=OWNER_ID,
        record_type="task_candidate",
        data={"title": "Synthetic task", "due": None},
        source_refs=(source_reference(evidence_location="body:12-24"),),
        processing_version="rules-v1",
        created_at=NOW,
    )
    assert record.verification.value == "unverified"
    assert record.confidence is None

    with pytest.raises(ValidationError, match="owner_id"):
        KnowledgeRecord(
            id=uuid4(),
            owner_id=OWNER_ID,
            record_type="task_candidate",
            data={},
            source_refs=(source_reference(owner_id=OWNER_ID + 1),),
            processing_version="rules-v1",
            created_at=NOW,
        )
    with pytest.raises(ValidationError):
        KnowledgeRecord(
            id=uuid4(),
            owner_id=OWNER_ID,
            record_type="task_candidate",
            data={},
            source_refs=(),
            processing_version="rules-v1",
            created_at=NOW,
        )


def test_entity_and_relationship_require_owner_scoped_evidence():
    entity = Entity(
        id=uuid4(),
        owner_id=OWNER_ID,
        entity_type="person",
        canonical_name="Sam Example",
        aliases=("Sam",),
        evidence=(source_reference(),),
        created_at=NOW,
    )
    edge = Relationship(
        id=uuid4(),
        owner_id=OWNER_ID,
        subject_id=entity.id,
        object_id=uuid4(),
        relation_type="mentioned_with",
        evidence=(source_reference(),),
        created_at=NOW,
    )
    assert entity.aliases == ("Sam",)
    assert edge.confidence is None

    with pytest.raises(ValidationError, match="aliases"):
        Entity(
            id=uuid4(),
            owner_id=OWNER_ID,
            entity_type="person",
            canonical_name="Sam Example",
            aliases=("Sam", " sam "),
            evidence=(source_reference(),),
            created_at=NOW,
        )
    with pytest.raises(ValidationError, match="endpoints"):
        Relationship(
            id=uuid4(),
            owner_id=OWNER_ID,
            subject_id=entity.id,
            object_id=entity.id,
            relation_type="depends_on",
            evidence=(source_reference(),),
            created_at=NOW,
        )


def test_deadline_requires_exactly_one_instant_or_date_and_preserves_zone():
    instant = Deadline(
        id=uuid4(),
        owner_id=OWNER_ID,
        record_id=uuid4(),
        due_at=datetime(2026, 11, 12, 9, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))),
        source_timezone="Asia/Kolkata",
        evidence=(source_reference(),),
        created_at=NOW,
    )
    date_only = Deadline(
        id=uuid4(),
        owner_id=OWNER_ID,
        record_id=uuid4(),
        due_date=date(2026, 11, 12),
        evidence=(source_reference(),),
        created_at=NOW,
    )
    assert instant.due_at == datetime(2026, 11, 12, 3, 30, tzinfo=UTC)
    assert instant.source_timezone == "Asia/Kolkata"
    assert date_only.due_date == date(2026, 11, 12)
    assert date_only.due_at is None

    with pytest.raises(ValidationError, match="exactly one"):
        Deadline(
            id=uuid4(),
            owner_id=OWNER_ID,
            record_id=uuid4(),
            evidence=(source_reference(),),
            created_at=NOW,
        )
    with pytest.raises(ValidationError):
        Deadline(
            id=uuid4(),
            owner_id=OWNER_ID,
            record_id=uuid4(),
            due_date=NOW,
            evidence=(source_reference(),),
            created_at=NOW,
        )


def test_feedback_action_payload_and_timezone_rules():
    corrected = Feedback(
        id=uuid4(),
        owner_id=OWNER_ID,
        target_type="task",
        target_id="123",
        action=FeedbackAction.CORRECT,
        value=None,
        occurred_at=NOW,
    )
    snooze = Feedback(
        id=uuid4(),
        owner_id=OWNER_ID,
        target_type="recommendation",
        target_id="rec-1",
        action=FeedbackAction.SNOOZE,
        snoozed_until=NOW + timedelta(days=1),
        occurred_at=NOW,
    )
    assert corrected.target_id == "123"
    assert snooze.snoozed_until == NOW + timedelta(days=1)

    with pytest.raises(ValidationError, match="requires a value"):
        Feedback(
            id=uuid4(),
            owner_id=OWNER_ID,
            target_type="task",
            target_id="123",
            action=FeedbackAction.CORRECT,
            occurred_at=NOW,
        )
    with pytest.raises(ValidationError, match="requires snoozed_until"):
        Feedback(
            id=uuid4(),
            owner_id=OWNER_ID,
            target_type="task",
            target_id="123",
            action=FeedbackAction.SNOOZE,
            occurred_at=NOW,
        )


def test_contract_json_round_trip():
    record = raw_record(source_timezone="Asia/Kolkata")
    restored = RawRecord.model_validate_json(record.model_dump_json())
    assert restored == record
    assert restored.ingested_at.tzinfo == UTC
