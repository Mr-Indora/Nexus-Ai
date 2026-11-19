"""Owner-bound knowledge review operations; authentication remains an app boundary."""

from uuid import UUID

from pids.contracts import KnowledgeConflict, KnowledgeLifecycleState, KnowledgeRecord, KnowledgeReviewEvent
from pids.identity import AuthenticatedPrincipal, owner_id_from_principal
from pids.persistence.repositories import KnowledgeLifecycleRepository


class KnowledgeLifecycleService:
    def __init__(self, repository: KnowledgeLifecycleRepository):
        self.repository = repository

    def transition(
        self,
        principal: AuthenticatedPrincipal | None,
        knowledge_id: UUID | str,
        *,
        expected_version: int,
        to_state: KnowledgeLifecycleState,
        decision: str,
        request_id: str,
        rejection_reason: str | None = None,
        superseded_by_id: UUID | str | None = None,
    ) -> tuple[KnowledgeRecord, KnowledgeReviewEvent]:
        reviewer_id = owner_id_from_principal(principal)
        if not decision or len(decision) > 64:
            raise ValueError("review decision must contain 1 to 64 characters")
        return self.repository.transition(
            reviewer_id,
            knowledge_id,
            expected_version=expected_version,
            to_state=to_state,
            reviewer_id=reviewer_id,
            decision=decision,
            request_id=request_id,
            rejection_reason=rejection_reason,
            superseded_by_id=superseded_by_id,
        )

    def record_conflict(
        self,
        principal: AuthenticatedPrincipal | None,
        existing_id: UUID | str,
        candidate_id: UUID | str,
        *,
        conflict_type: str,
        description: str,
    ) -> KnowledgeConflict:
        owner_id = owner_id_from_principal(principal)
        return self.repository.create_conflict(
            owner_id,
            existing_id,
            candidate_id,
            conflict_type,
            description,
        )
