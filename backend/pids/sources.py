"""Owner-scoped source registration and deterministic connector contracts."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterator
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from pids.contracts import Source, SourceKind, SourceStatus
from pids.normalization import IncomingRecord
from pids.persistence.database import utc_now
from pids.persistence.repositories import PidsNotFoundError, SourceRepository


class ConnectorError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool):
        if not re.fullmatch(r"[a-z0-9_]{1,64}", code):
            raise ValueError("connector error code must be a safe identifier")
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class TextConnector(Protocol):
    """Minimal read contract for local/manual adapters; no network behavior."""

    kind: SourceKind
    capabilities: frozenset[str]

    def fetch(self, source: Source, *, cursor: str | None = None) -> Iterator[IncomingRecord]:
        ...


class SourceRegistry:
    def __init__(self, repository: SourceRepository):
        self.repository = repository

    def register(
        self,
        owner_id: int,
        kind: SourceKind,
        display_name: str,
        identity_key: str,
        *,
        account_ref: str | None = None,
    ) -> Source:
        if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id <= 0:
            raise ValueError("owner_id must be a positive integer")
        kind = SourceKind(kind)
        if not isinstance(identity_key, str):
            raise ValueError("identity_key must be non-secret text")
        identity = unicodedata.normalize("NFC", identity_key.strip())
        if not identity or len(identity) > 512:
            raise ValueError("identity_key must contain 1 to 512 characters")
        identity_digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        source_id = uuid5(
            NAMESPACE_URL, f"pids-source-v1:{owner_id}:{kind.value}:{identity_digest}"
        )
        now = utc_now()
        source = Source(
            id=source_id,
            owner_id=owner_id,
            kind=kind,
            display_name=display_name,
            account_ref=account_ref,
            created_at=now,
            updated_at=now,
        )
        try:
            return self.repository.get(owner_id, source_id)
        except PidsNotFoundError:
            return self.repository.create(source)

    def get(self, owner_id: int, source_id: UUID) -> Source:
        return self.repository.get(owner_id, source_id)

    def set_status(self, owner_id: int, source_id: UUID, status: SourceStatus) -> Source:
        return self.repository.update(owner_id, source_id, status=status)
