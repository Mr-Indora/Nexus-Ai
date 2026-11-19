from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pids.contracts import KnowledgeRecord, Source, SourceKind, SourceReference
from pids.identity import bind_authenticated_owner, owner_id_from_principal


NOW = datetime(2026, 11, 12, 4, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "principal",
    [
        None,
        SimpleNamespace(id=0),
        SimpleNamespace(id=-1),
        SimpleNamespace(id=True),
        SimpleNamespace(id="17"),
    ],
)
def test_missing_or_invalid_authenticated_owner_fails_closed(principal):
    with pytest.raises(ValueError, match="principal"):
        owner_id_from_principal(principal)


def test_client_record_owner_is_replaced_by_authenticated_principal():
    client_record = Source(
        id=uuid4(),
        owner_id=29,
        kind=SourceKind.MANUAL,
        display_name="Synthetic source",
        created_at=NOW,
        updated_at=NOW,
    )

    bound = bind_authenticated_owner(client_record, SimpleNamespace(id=17))

    assert bound.owner_id == 17
    assert client_record.owner_id == 29


def test_cross_owner_provenance_is_rejected_when_binding_authenticated_owner():
    client_record = KnowledgeRecord(
        id=uuid4(),
        owner_id=29,
        record_type="task_candidate",
        data={"title": "Synthetic"},
        source_refs=(
            SourceReference(owner_id=29, source_id=uuid4(), raw_record_id=uuid4()),
        ),
        processing_version="rules-v1",
        created_at=NOW,
    )

    with pytest.raises(ValidationError, match="owner_id"):
        bind_authenticated_owner(client_record, SimpleNamespace(id=17))
