from datetime import date, datetime, timezone
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from integrations import classroom, college
from pids.academic_adapter import _deadline_date, _deadline_priority, ingest_academic_items, record_academic_sync_failure
from pids.contracts import SourceKind
from pids.nexus_adapter import ingest_gmail_batch, record_google_sync_failure
from pids.persistence.database import ISOLATED_WORKSPACE, create_pids_engine, create_session_factory
from pids.persistence.models import KnowledgeRecordRow, RecommendationRow, SourceRow, SourceSyncStateRow, TaskCandidateRow
from pids.vault import RawDataVault
from routes.pids import _academic_brief_line, _new_academic_updates

UTC = timezone.utc
MIGRATIONS = Path(__file__).resolve().parents[1] / "pids" / "persistence" / "alembic.ini"


@pytest.fixture
def academic_store():
    ISOLATED_WORKSPACE.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    database = ISOLATED_WORKSPACE / f"test-academic-{token}.db"
    vault_root = ISOLATED_WORKSPACE / f"test-academic-vault-{token}"
    config = Config(str(MIGRATIONS))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")
    command.upgrade(config, "head")
    engine = create_pids_engine(f"sqlite:///{database.as_posix()}")
    session = create_session_factory(engine)()
    try:
        yield session, RawDataVault(vault_root), vault_root
    finally:
        session.rollback()
        session.close()
        engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            Path(f"{database}{suffix}").unlink(missing_ok=True)
        if vault_root.exists():
            shutil.rmtree(vault_root)


def test_college_deadline_extraction_requires_deadline_cue_and_unambiguous_date():
    assert _deadline_date("Exam form filling. Last date 01.11.2026 with single fee.") == date(2026, 11, 1)
    assert _deadline_date("Submit the form by 2026-12-05.") == date(2026, 12, 5)
    assert _deadline_date("The examination will be held on 2026-12-05.") is None
    assert _deadline_date("Pay fee by 01/02/2027.") == date(2027, 2, 1)
    assert _deadline_date("अंतिम तिथि तक आवेदन जमा करें ०१/११/२०२६") == date(2026, 11, 1)


def test_academic_deadline_priority_does_not_keep_old_notices_urgent():
    today = date(2026, 11, 17)
    assert _deadline_priority(date(2026, 11, 17), today=today) == "high"
    assert _deadline_priority(date(2026, 11, 20), today=today) == "high"
    assert _deadline_priority(date(2026, 11, 21), today=today) == "normal"
    assert _deadline_priority(date(2026, 11, 16), today=today) == "high"
    assert _deadline_priority(date(2026, 11, 14), today=today) == "high"
    assert _deadline_priority(date(2026, 11, 13), today=today) == "normal"
    assert _deadline_priority(date(2026, 11, 12), today=today) == "normal"
    assert _deadline_priority(date(2025, 11, 16), today=today) == "normal"
    assert _deadline_priority(None, today=today) == "normal"


def test_academic_gemini_summary_is_consent_gated_and_validated(monkeypatch):
    from ai import academic
    from config import settings

    monkeypatch.setattr(settings, "GEMINI_API_KEY", "synthetic-key")
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        payload = [{
            "external_id": "notice-1", "topic": "Examination Form",
            "summary": "Students must submit the exam form by 20 November.",
            "needs_action": True, "action_title": "Submit exam form",
        }]
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"candidates": [{"content": {"parts": [{"text": json.dumps({"items": payload})}]}}]},
        )

    monkeypatch.setattr(academic.httpx, "post", fake_post)
    records = [{
        "external_id": "notice-1", "record_type": "college_notice",
        "title": "Exam form", "body": "Submit the exam form by 20 November.",
    }]
    denied, status = academic.summarize_academic_batch(records, consented=False, owner_id=741)
    assert denied == {} and status == "consent_required" and calls == []
    result, status = academic.summarize_academic_batch(records, consented=True, owner_id=741)
    assert status == "available"
    assert result["notice-1"]["summary"].startswith("Students must submit")
    assert result["notice-1"]["needs_action"] is True
    assert "untrusted source data" in calls[0][1]["json"]["contents"][0]["parts"][0]["text"]

    def hallucinated_date(url, **kwargs):
        payload = [{
            "external_id": "notice-1", "topic": "Examination Form",
            "summary": "Students must submit by 25 November.",
            "needs_action": True, "action_title": "Submit exam form by 25 November",
        }]
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"candidates": [{"content": {"parts": [{"text": json.dumps({"items": payload})}]}}]},
        )

    monkeypatch.setattr(academic.httpx, "post", hallucinated_date)
    rejected, status = academic.summarize_academic_batch(records, consented=True, owner_id=741)
    assert rejected == {} and status == "invalid_response"


def test_academic_gemini_summary_enriches_ingestion_without_trusting_model_dates(academic_store, monkeypatch):
    import pids.academic_adapter as adapter

    session, vault, _ = academic_store
    principal = SimpleNamespace(id=742, external_ai_consent=True)
    item = {
        "external_id": "notice-ai-summary", "source_version": "v1",
        "record_type": "college_notice", "title": "Exam form",
        "body": "Submit the exam form by 2026-11-20.",
        "url": "https://ecajmer.ac.in/uploads/circulars/form.pdf",
    }
    calls = []

    def fake_summary(records, *, consented, owner_id):
        calls.append((records, consented, owner_id))
        return {"notice-ai-summary": {
            "topic": "Examination Form", "summary": "Submit the exam form before the listed deadline.",
            "needs_action": True, "action_title": "Submit examination form",
        }}, "available"

    monkeypatch.setattr(adapter, "summarize_academic_batch", fake_summary)
    result = ingest_academic_items(
        session, principal, [item], source_kind=SourceKind.COLLEGE_NOTICES,
        source_name="Engineering College Ajmer circulars", identity_key="eca:ai-summary", vault=vault,
    )
    knowledge = session.scalar(select(KnowledgeRecordRow).where(KnowledgeRecordRow.owner_id == 742))
    candidate = session.scalar(select(TaskCandidateRow).where(TaskCandidateRow.owner_id == 742))
    assert result["aiSummarized"] == 1 and result["aiStatus"] == "available"
    assert len(calls) == 1 and calls[0][1:] == (True, 742)
    assert knowledge.data["summary"] == "Submit the exam form before the listed deadline."
    assert knowledge.data["analysis_method"] == "gemini_academic_summary_v1"
    assert knowledge.data["due_date"] == "2026-11-20"
    assert candidate.title == "Submit examination form"
    assert candidate.due_date == date(2026, 11, 20)


def test_college_feed_parses_official_json_and_keeps_document_link(monkeypatch):
    class Response:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"success": True, "data": [{
                "id": 44, "sno": "2026WEB0044", "title": "Exam form last date 01.11.2026",
                "content": "uploads/circulars/notice.pdf", "contentType": "pdf",
                "date": "2026-10-30", "updatedAt": "2026-10-30T10:00:00Z",
                "department": {"name": "Computer Science"}, "isDeleted": 0,
            }]}

    monkeypatch.setattr(college.httpx, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(college, "extract_pdf_text", lambda *args, **kwargs: "Submit the form by 2026-11-01.")
    items = college.fetch_circulars(limit=1)
    assert items[0]["external_id"] == "44"
    assert items[0]["url"] == "https://ecajmer.ac.in/uploads/circulars/notice.pdf"
    assert items[0]["content_extracted"] is True
    assert items[0]["published_at"].tzinfo is not None


def test_college_feed_keeps_all_notice_links_but_bounds_pdf_reads(monkeypatch):
    class Response:
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"success": True, "data": [{
                "id": index, "title": f"Notice {index}", "content": f"uploads/{index}.pdf",
                "contentType": "pdf", "date": "2026-11-17", "isDeleted": 0,
            } for index in range(1, 9)]}

    calls = []
    monkeypatch.setattr(college.httpx, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(college, "extract_pdf_text", lambda client, url: calls.append(url) or "")
    items = college.fetch_circulars(limit=8)
    assert len(items) == 8
    assert len(calls) == college.MAX_PDF_DOWNLOADS_PER_SYNC
    assert all(item["url"].startswith("https://ecajmer.ac.in/") for item in items)


def test_classroom_connector_fetches_published_work_materials_and_announcements():
    class Client:
        def __init__(self):
            self.calls = []
        def get(self, url, params=None):
            self.calls.append((url, params))
            if url.endswith("/courses"):
                return {"courses": [{"id": "course-1", "name": "Networks", "courseState": "ACTIVE"}]}
            if url.endswith("/courseWork"):
                return {"courseWork": [{
                    "id": "work-2", "title": "Submit lab report", "description": "Upload the completed report.",
                    "updateTime": "2026-11-10T10:00:00Z", "dueDate": {"year": 2026, "month": 11, "day": 18},
                    "alternateLink": "https://classroom.google.com/c/course-1/a/work-2",
                    "state": "PUBLISHED", "creatorUserId": "teacher-1",
                }]}
            if url.endswith("/courseWorkMaterials"):
                return {"courseWorkMaterial": [{
                    "id": "material-4", "title": "Week 7 lecture notes",
                    "description": "Review before the quiz.",
                    "updateTime": "2026-11-10T11:00:00Z",
                    "alternateLink": "https://classroom.google.com/c/course-1/m/material-4",
                    "state": "PUBLISHED", "creatorUserId": "teacher-1",
                    "materials": [{"link": {"title": "Reference article", "url": "https://example.edu/article"}}],
                }]}
            return {"announcements": [{"id": "announcement-3", "text": "Tomorrow is a holiday.", "creationTime": "2026-11-10T09:00:00Z", "creatorUserId": "teacher-1"}]}

    client = Client()
    items = classroom.fetch_classroom_items(client)
    assert [item["record_type"] for item in items] == ["classroom_coursework", "classroom_material", "classroom_announcement"]
    assert items[0]["due_date"] == "2026-11-18"
    assert "Reference article" in items[1]["body"]
    assert items[1]["url"].endswith("material-4")
    assert items[0]["author"] == "Course instructor"
    materials_call = next(call for call in client.calls if call[0].endswith("/courseWorkMaterials"))
    assert materials_call[1]["courseWorkMaterialStates"] == "PUBLISHED"
    assert len(client.calls) == 4


def test_classroom_pages_continue_with_the_previous_page_token():
    class Client:
        def __init__(self):
            self.calls = []
        def get(self, url, params=None):
            self.calls.append(params)
            if params.get("pageToken"):
                return {"courseWorkMaterial": [{"id": f"second-{index}"} for index in range(25)]}
            return {
                "courseWorkMaterial": [{"id": f"first-{index}"} for index in range(25)],
                "nextPageToken": "page-2",
            }

    client = Client()
    rows = classroom._pages(
        client, "https://classroom.googleapis.com/v1/courses/1/courseWorkMaterials",
        params={"orderBy": "updateTime desc"}, rows_key="courseWorkMaterial",
    )
    assert len(rows) == 50
    assert client.calls[1]["pageToken"] == "page-2"


def test_academic_ingestion_is_owner_scoped_encrypted_and_idempotent(academic_store):
    session, vault, _ = academic_store
    principal = SimpleNamespace(id=731)
    item = {
        "external_id": "eca-2026-1", "source_version": "2026-11-10T12:00:00Z",
        "record_type": "college_notice", "title": "Submit exam form by 2026-11-20",
        "body": "All students must submit the form by 2026-11-20.",
        "url": "https://ecajmer.ac.in/uploads/circulars/exam.pdf",
        "published_at": datetime(2026, 11, 10, tzinfo=UTC), "content_extracted": True,
    }
    first = ingest_academic_items(
        session, principal, [item], source_kind=SourceKind.COLLEGE_NOTICES,
        source_name="Engineering College Ajmer circulars", identity_key="eca:public-feed", vault=vault,
    )
    replay = ingest_academic_items(
        session, principal, [item], source_kind=SourceKind.COLLEGE_NOTICES,
        source_name="Engineering College Ajmer circulars", identity_key="eca:public-feed", vault=vault,
    )
    assert first["processed"] == 1 and first["tasks"] == 1 and first["notifications"] == 1
    assert replay["processed"] == 0 and replay["replayed"] == 1
    task = session.scalar(select(TaskCandidateRow).where(TaskCandidateRow.owner_id == 731))
    notice = session.scalar(select(KnowledgeRecordRow).where(KnowledgeRecordRow.owner_id == 731))
    recommendation = session.scalar(select(RecommendationRow).where(RecommendationRow.owner_id == 731))
    assert task.due_date == date(2026, 11, 20)
    assert task.review_state == "needs_review"
    assert task.evidence["url"] == item["url"]
    assert notice.data["content_extracted"] is True
    assert recommendation.evidence["url"] == item["url"]
    assert session.scalar(select(TaskCandidateRow).where(TaskCandidateRow.owner_id == 732)) is None


def test_academic_replay_recalculates_only_pending_priority(academic_store, monkeypatch):
    import pids.academic_adapter as adapter

    session, vault, _ = academic_store
    principal = SimpleNamespace(id=733)
    item = {
        "external_id": "eca-priority-replay", "source_version": "v1",
        "record_type": "college_notice", "title": "Submit exam form",
        "body": "Submit the exam form by 2026-11-20.",
        "url": "https://ecajmer.ac.in/uploads/circulars/form.pdf",
        "due_date": "2026-11-20",
    }
    monkeypatch.setattr(adapter, "_deadline_priority", lambda due_date: "high")
    ingest_academic_items(
        session, principal, [item], source_kind=SourceKind.COLLEGE_NOTICES,
        source_name="Engineering College Ajmer circulars", identity_key="eca:priority-replay", vault=vault,
    )
    candidate = session.scalar(select(TaskCandidateRow).where(TaskCandidateRow.owner_id == 733))
    recommendation = session.scalar(select(RecommendationRow).where(RecommendationRow.owner_id == 733))
    assert candidate.priority == recommendation.priority == "high"

    monkeypatch.setattr(adapter, "_deadline_priority", lambda due_date: "normal")
    replay = ingest_academic_items(
        session, principal, [item], source_kind=SourceKind.COLLEGE_NOTICES,
        source_name="Engineering College Ajmer circulars", identity_key="eca:priority-replay", vault=vault,
    )
    assert replay["replayed"] == 1
    assert candidate.priority == recommendation.priority == "normal"

    candidate.review_state = "approved"
    recommendation.status = "approved"
    monkeypatch.setattr(adapter, "_deadline_priority", lambda due_date: "high")
    ingest_academic_items(
        session, principal, [item], source_kind=SourceKind.COLLEGE_NOTICES,
        source_name="Engineering College Ajmer circulars", identity_key="eca:priority-replay", vault=vault,
    )
    assert candidate.priority == recommendation.priority == "normal"


def test_classroom_material_action_becomes_reviewable_task_candidate(academic_store):
    session, vault, _ = academic_store
    principal = SimpleNamespace(id=735)
    item = {
        "external_id": "material:course-1:week-7-practice", "source_version": "v1",
        "record_type": "classroom_material", "title": "Complete Week 7 practice set",
        "body": "Complete these problems before the next class.",
        "url": "https://classroom.google.com/c/course-1/m/week-7-practice",
    }
    result = ingest_academic_items(
        session, principal, [item], source_kind=SourceKind.GOOGLE_CLASSROOM,
        source_name="Google Classroom", identity_key="google-classroom:user:735", vault=vault,
    )
    candidate = session.scalar(select(TaskCandidateRow).where(TaskCandidateRow.owner_id == 735))
    assert result["tasks"] == 1
    assert candidate is not None and candidate.review_state == "needs_review"
    assert candidate.evidence["url"] == item["url"]


def test_google_sync_failure_updates_attempt_and_keeps_last_success_and_cursor(academic_store):
    session, vault, _ = academic_store
    principal = SimpleNamespace(id=741)
    ingest_gmail_batch(session, principal, [], vault=vault, history_id="history-123")
    source_row = session.scalar(select(SourceRow).where(
        SourceRow.owner_id == 741, SourceRow.kind == SourceKind.GMAIL.value,
    ))
    assert source_row is not None
    state = session.get(SourceSyncStateRow, (741, source_row.id))
    last_success = state.last_success_at
    assert state.cursor == "history-123"

    record_google_sync_failure(
        session, principal, source_kind=SourceKind.GMAIL, error_code="permission_required",
    )

    session.refresh(state)
    assert state.status == "error"
    assert state.last_error_code == "permission_required"
    assert state.last_attempt_at is not None
    assert state.last_success_at == last_success
    assert state.cursor == "history-123"


def test_daily_brief_digest_counts_only_items_from_latest_successful_check(academic_store):
    session, vault, _ = academic_store
    principal = SimpleNamespace(id=736)
    item = {
        "external_id": "classroom-announcement-1", "source_version": "v1",
        "record_type": "classroom_announcement", "title": "Quiz moved to Friday",
        "body": "The quiz will now take place on Friday.",
        "url": "https://classroom.google.com/c/course-1/a/announcement-1",
    }
    ingest_academic_items(
        session, principal, [item], source_kind=SourceKind.GOOGLE_CLASSROOM,
        source_name="Google Classroom", identity_key="google-classroom:user:736", vault=vault,
    )
    digest = _new_academic_updates(
        session, 736, "google_classroom",
        ("classroom_coursework", "classroom_announcement", "classroom_material"),
    )
    assert digest["count"] == 1
    assert digest["items"][0]["title"] == "Quiz moved to Friday"
    line = _academic_brief_line(
        {"count": 0, "items": [], "lastSuccessfulCheckAt": None, "latestCheckFailed": False},
        digest,
    )
    assert "1 new Classroom update(s)" in line
    assert "Quiz moved to Friday" in line

    record_academic_sync_failure(
        session, principal, source_kind=SourceKind.GOOGLE_CLASSROOM,
        source_name="Google Classroom", identity_key="google-classroom:user:736",
        error_code="classroom_permission_required",
    )
    stale_digest = _new_academic_updates(
        session, 736, "google_classroom",
        ("classroom_coursework", "classroom_announcement", "classroom_material"),
    )
    assert stale_digest["count"] == 0
    assert stale_digest["items"] == []
    failed_line = _academic_brief_line(
        {"count": 0, "items": [], "lastSuccessfulCheckAt": None, "latestCheckFailed": False},
        stale_digest,
    )
    assert "failed" in failed_line.lower()
    assert "unknown" in failed_line.lower()


def test_failed_classroom_sync_is_saved_as_owner_scoped_status(academic_store):
    session, _, _ = academic_store
    principal = SimpleNamespace(id=732)
    record_academic_sync_failure(
        session, principal, source_kind=SourceKind.GOOGLE_CLASSROOM,
        source_name="Google Classroom", identity_key="google-classroom:user:732",
        error_code="classroom_permission_required",
    )
    state = session.scalar(select(SourceSyncStateRow).where(SourceSyncStateRow.owner_id == 732))
    assert state is not None
    assert state.status == "error"
    assert state.last_attempt_at is not None
    assert state.last_error_code == "classroom_permission_required"

