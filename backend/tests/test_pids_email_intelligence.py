from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import shutil
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from pids.contracts import SourceKind
from pids.email_intelligence import EmailIntelligenceService, EmailMessageInput, analyze_email
from pids.nexus_adapter import ingest_gmail_batch
from pids.normalization import normalize_text
from pids.persistence.database import (
    ISOLATED_WORKSPACE,
    create_pids_engine,
    create_session_factory,
)
from pids.persistence.models import (
    EmailMessageRow,
    EvidenceReferenceRow,
    RecommendationRow,
    TaskCandidateRow,
    ThreadIntelligenceRow,
)
from pids.persistence.repositories import PidsRepositories
from pids.sources import SourceRegistry
from pids.vault import RawDataVault
from routes.pids import (
    RecommendationReview,
    TaskCandidateCorrection,
    TaskCandidateReview,
    list_emails,
    overview,
    review_recommendation,
    review_task_candidate,
)

UTC = timezone.utc
NOW = datetime(2026, 11, 12, 4, 0, tzinfo=UTC)
ALEMBIC_CONFIG = Path(__file__).resolve().parents[1] / "pids" / "persistence" / "alembic.ini"


@pytest.fixture
def isolated_session():
    ISOLATED_WORKSPACE.mkdir(parents=True, exist_ok=True)
    path = ISOLATED_WORKSPACE / f"test-email-{uuid4().hex}.db"
    vault_root = ISOLATED_WORKSPACE / f"test-email-vault-{uuid4().hex}"
    url = f"sqlite:///{path.as_posix()}"
    config = Config(str(ALEMBIC_CONFIG))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine = create_pids_engine(url)
    session = create_session_factory(engine)()
    try:
        yield session, vault_root
    finally:
        session.rollback()
        session.close()
        engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            generated = Path(f"{path}{suffix}")
            if generated.exists():
                generated.unlink()
        if vault_root.exists():
            shutil.rmtree(vault_root)


def register_source(session, owner_id):
    return SourceRegistry(PidsRepositories(session).sources).register(
        owner_id,
        SourceKind.GMAIL,
        "Synthetic email source",
        "synthetic-email",
        account_ref="synthetic:test",
    )


def message(source, external_id, subject, body, *, thread_id=None, direction="incoming", at=NOW):
    return EmailMessageInput(
        owner_id=source.owner_id,
        source_id=source.id,
        external_id=external_id,
        text=body,
        sender="sender@example.test",
        subject=subject,
        thread_id=thread_id,
        direction=direction,
        occurred_at=at,
        source_timezone="Asia/Kolkata",
    )


def test_email_intelligence_is_conservative_and_ignores_quoted_requests():
    sample = EmailMessageInput(
        owner_id=501,
        source_id=uuid4(),
        external_id="email-analysis-1",
        text="The workshop is on 2026-11-18.\n\n> Please submit the report by 2026-11-19.",
        sender="sender@example.test",
        subject="Workshop details",
        occurred_at=NOW,
        source_timezone="Asia/Kolkata",
    )
    result = analyze_email(sample)
    assert result.reply_state == "reply_optional"
    assert not result.actions
    assert result.important_dates[0]["deadline"] is False
    assert "2026-11-19" not in result.summary
    assert type(result).model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("sender", ["donot_reply@study.iitm.ac.in", "noreply@example.test"])
def test_automated_assignment_email_is_an_action_not_a_reply_request(sender):
    sample = EmailMessageInput(
        owner_id=503,
        source_id=uuid4(),
        external_id="automated-assignment",
        text="Week 7 content and graded assignment released. Please Check. Deadline 2026-11-25 23:59. Unsubscribe from notices.",
        sender=sender,
        subject="Assignment released",
        occurred_at=NOW,
        source_timezone="Asia/Kolkata",
    )
    result = analyze_email(sample)
    assert result.reply_state == "no_reply_required"
    assert "automated sender" in (result.reply_reason or "")
    assert result.importance != "low"
    assert len(result.actions) == 1
    assert result.actions[0].title == "Submit Week 7 graded assignment"
    assert result.actions[0].due_date == "2026-11-25"


def test_generic_please_check_without_a_concrete_action_is_not_a_task():
    sample = EmailMessageInput(
        owner_id=504,
        source_id=uuid4(),
        external_id="generic-check",
        text="Please Check.",
        sender="donot_reply@example.test",
        subject="Weekly notice",
        occurred_at=NOW,
        source_timezone="Asia/Kolkata",
    )
    result = analyze_email(sample)
    assert result.actions == ()
    assert result.importance == "informational"


def test_bulk_newsletter_call_to_action_is_not_a_required_reply():
    sample = EmailMessageInput(
        owner_id=504,
        source_id=uuid4(),
        external_id="newsletter-action",
        text="Are these jobs relevant? Yes / No. Unsubscribe from this newsletter.",
        sender="jobs@bulk.example.test",
        subject="Recommended jobs for you",
        occurred_at=NOW,
        source_timezone="Asia/Kolkata",
    )
    result = analyze_email(sample)
    assert result.reply_state != "reply_required"


def test_action_evidence_offsets_match_normalized_message_text():
    source_id = uuid4()
    sample = EmailMessageInput(
        owner_id=502,
        source_id=source_id,
        external_id="email-offsets",
        text=" \r\nPlease submit the report by 2026-11-18.\r\n",
        sender="sender@example.test",
        subject="  Report request  ",
        occurred_at=NOW,
        source_timezone="Asia/Kolkata",
    )
    result = analyze_email(sample)
    normalized = normalize_text(sample.message_text)
    action = result.actions[0]
    assert normalized[action.start:action.end] == action.evidence


def test_email_candidates_persist_with_evidence_and_are_idempotent(isolated_session):
    session, vault_root = isolated_session
    source = register_source(session, 511)
    sample = message(
        source, "email-1", "Report request",
        "Please submit the report by 2026-11-18 and confirm when sent.",
        thread_id="thread-1",
    )
    service = EmailIntelligenceService(session, vault=RawDataVault(vault_root))
    result, knowledge_id, raw_id = service.process_email(sample, source)
    replay, replay_knowledge_id, replay_raw_id = service.process_email(sample, source)
    assert result.reply_state == "reply_required"
    assert result.actions[0].due_date == "2026-11-18"
    assert replay == result
    assert replay_knowledge_id == knowledge_id
    assert replay_raw_id == raw_id
    knowledge = PidsRepositories(session).knowledge_records.get(511, knowledge_id)
    assert knowledge.record_type == "email_intelligence"
    assert knowledge.verification.value == "needs_review"
    evidence = session.query(EvidenceReferenceRow).filter_by(
        owner_id=511, knowledge_id=str(knowledge_id)
    ).one()
    assert evidence.raw_record_id == str(raw_id)


def test_nexus_gmail_adapter_is_owner_bound_and_idempotent(isolated_session, monkeypatch):
    session, vault_root = isolated_session
    principal = SimpleNamespace(id=541)
    message_data = {
        "gmail_id": "synthetic-gmail-1",
        "thread_id": "synthetic-thread-1",
        "sender": "recruiter@example.test",
        "subject": "Interview request",
        "snippet": "Please confirm your availability by 2026-11-20.",
        "body": "Please confirm your interview availability by 2026-11-20.",
        "received_at": NOW.replace(tzinfo=None),
        "labels": ["INBOX"],
    }
    vault = RawDataVault(vault_root)

    first = ingest_gmail_batch(session, principal, [message_data], vault=vault)
    replay = ingest_gmail_batch(session, principal, [message_data], vault=vault)

    assert first["processed"] == 1 and first["failed"] == 0
    assert replay["processed"] == 1 and replay["replayed"] == 1
    assert session.query(EmailMessageRow).filter_by(owner_id=541).count() == 1
    assert session.query(ThreadIntelligenceRow).filter_by(owner_id=541).count() == 1
    assert session.query(TaskCandidateRow).filter_by(owner_id=541).count() >= 1
    assert session.query(RecommendationRow).filter_by(owner_id=541).count() >= 1
    dashboard = overview(principal, session)
    assert dashboard["emails"][0]["subject"] == "Interview request"
    assert dashboard["recommendations"]
    detail = list_emails(limit=20, offset=0, user=principal, db=session)
    assert detail[0]["analysis"]["reply_state"] == "reply_required"
    other_owner = overview(SimpleNamespace(id=542), session)
    assert other_owner["emails"] == []
    recommendation_id = dashboard["recommendations"][0]["id"]
    decision = review_recommendation(
        recommendation_id, RecommendationReview(action="approve"), principal, session
    )
    assert decision["status"] == "approved"
    assert review_recommendation(
        recommendation_id, RecommendationReview(action="approve"), principal, session
    )["changed"] is False
    candidate = session.query(TaskCandidateRow).filter_by(owner_id=541).first()
    reviewed_task = review_task_candidate(
        candidate.id,
        TaskCandidateReview(
            action="approve",
            correction=TaskCandidateCorrection(title="Confirm interview slot", due_date="2026-11-21"),
        ),
        principal,
        session,
    )
    assert reviewed_task["reviewState"] == "approved"
    assert session.query(TaskCandidateRow).filter_by(id=candidate.id).one().title == "Confirm interview slot"


def test_nexus_gmail_adapter_rejects_client_supplied_owner(isolated_session):
    session, vault_root = isolated_session
    with pytest.raises(ValueError, match="authenticated principal"):
        ingest_gmail_batch(
            session,
            None,
            [],
            vault=RawDataVault(vault_root),
        )


def test_nexus_adapter_rebuilds_thread_across_incremental_syncs(isolated_session):
    session, vault_root = isolated_session
    principal = SimpleNamespace(id=545)
    vault = RawDataVault(vault_root)
    first = {
        "gmail_id": "interview-sync-1",
        "thread_id": "interview-sync-thread",
        "sender": "hr@example.test",
        "subject": "Interview scheduled",
        "body": "Your interview is scheduled for 2026-11-16.",
        "received_at": datetime(2026, 11, 12, 4, 0, tzinfo=UTC),
    }
    second = {
        "gmail_id": "interview-sync-2",
        "thread_id": "interview-sync-thread",
        "sender": "hr@example.test",
        "subject": "Interview rescheduled",
        "body": "The interview is rescheduled from 2026-11-16 to 2026-11-19. Please confirm availability.",
        "received_at": datetime(2026, 11, 13, 4, 0, tzinfo=UTC),
    }
    ingest_gmail_batch(session, principal, [first], vault=vault)
    result = ingest_gmail_batch(session, principal, [second], vault=vault)
    thread = session.query(ThreadIntelligenceRow).filter_by(owner_id=545).one()
    assert result["failed"] == 0
    assert thread.analysis["deadlines"] == ["2026-11-19"]
    assert thread.analysis["superseded_deadlines"] == ["2026-11-16"]
def test_thread_intelligence_is_idempotent_and_preserves_owned_sources(isolated_session):
    session, vault_root = isolated_session
    source = register_source(session, 521)
    messages = (
        message(
            source, "thread-email-1", "Please send the draft",
            "Please send the draft by 2026-11-20.",
            thread_id="thread-2",
        ),
        message(
            source, "thread-email-2", "Re: Please send the draft",
            "I will send the draft tomorrow.",
            thread_id="thread-2", direction="outgoing",
            at=datetime(2026, 11, 13, 4, 0, tzinfo=UTC),
        ),
        message(
            source, "thread-email-3", "Re: Please send the draft",
            "I sent the draft.",
            thread_id="thread-2", direction="outgoing",
            at=datetime(2026, 11, 14, 4, 0, tzinfo=UTC),
        ),
    )
    service = EmailIntelligenceService(session, vault=RawDataVault(vault_root))
    result, knowledge_id = service.process_thread(source, messages)
    replay, replay_id = service.process_thread(source, messages)
    assert result == replay
    assert knowledge_id == replay_id
    assert result.message_count == 3
    assert result.latest_status == "user_replied"
    assert len(result.source_refs) == 3
    assert all(ref.owner_id == 521 for ref in result.source_refs)
    assert result.resolved_actions
    assert not result.pending_actions


def test_rescheduled_thread_date_supersedes_previous_interview_date(isolated_session):
    session, vault_root = isolated_session
    source = register_source(session, 525)
    messages = (
        message(
            source,
            "interview-date-1",
            "Interview scheduled",
            "Your interview is scheduled for 2026-11-16.",
            thread_id="interview-thread",
            at=datetime(2026, 11, 12, 4, 0, tzinfo=UTC),
        ),
        message(
            source,
            "interview-date-2",
            "Interview rescheduled",
            "The interview is rescheduled from 2026-11-16 to 2026-11-19. Please confirm availability.",
            thread_id="interview-thread",
            at=datetime(2026, 11, 13, 4, 0, tzinfo=UTC),
        ),
    )
    result, _ = EmailIntelligenceService(
        session, vault=RawDataVault(vault_root)
    ).process_thread(source, messages)
    assert result.deadlines == ("2026-11-19",)
    assert result.superseded_deadlines == ("2026-11-16",)
    assert not result.contradictions


def test_email_processing_rejects_mismatched_source_owner(isolated_session):
    session, vault_root = isolated_session
    source = register_source(session, 531)
    sample = message(source, "email-owner-check", "Hello", "Message.")
    foreign = sample.model_copy(update={"owner_id": 999})
    service = EmailIntelligenceService(session, vault=RawDataVault(vault_root))
    with pytest.raises(ValueError, match="owner/source"):
        service.process_email(foreign, source)
