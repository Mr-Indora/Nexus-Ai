"""Retire chat-history ORM/API usage while preserving existing records."""
from typing import Sequence, Union

revision: str = "0010_remove_assistant_history"
down_revision: Union[str, Sequence[str], None] = "0009_task_status_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Historical chat rows are retained as user data. The application no longer
    # exposes or reads them; dropping them would be an irreversible data loss.
    pass


def downgrade() -> None:
    # The migration never altered the retained legacy tables.
    pass
