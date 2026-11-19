"""Record owner-scoped task status transitions for productivity history."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009_task_status_events"
down_revision: Union[str, Sequence[str], None] = "0008_source_sync_freshness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_status_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_status", sa.String(length=16), nullable=False),
        sa.Column("to_status", sa.String(length=16), nullable=False),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_task_status_events_owner_time", "task_status_events", ["user_id", "changed_at"],
    )
    op.create_index(
        "ix_task_status_events_task_time", "task_status_events", ["task_id", "changed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_task_status_events_task_time", table_name="task_status_events")
    op.drop_index("ix_task_status_events_owner_time", table_name="task_status_events")
    op.drop_table("task_status_events")
