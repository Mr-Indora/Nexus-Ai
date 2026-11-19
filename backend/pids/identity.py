"""Framework-independent boundary for deriving PIDS ownership from auth."""

from typing import Protocol, TypeVar, cast

from pids.contracts import Contract


class AuthenticatedPrincipal(Protocol):
    """Server-authenticated principal shape; never construct from request data."""

    id: int


Record = TypeVar("Record", bound=Contract)


def owner_id_from_principal(principal: AuthenticatedPrincipal | None) -> int:
    if principal is None:
        raise ValueError("authenticated principal is required")
    owner_id = principal.id
    if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id <= 0:
        raise ValueError("authenticated principal has an invalid owner identity")
    return owner_id


def bind_authenticated_owner(
    record: Record, principal: AuthenticatedPrincipal | None
) -> Record:
    """Revalidate a domain record after replacing any caller-provided owner."""
    data = record.model_dump(mode="python")
    data["owner_id"] = owner_id_from_principal(principal)
    return cast(Record, type(record).model_validate(data))
