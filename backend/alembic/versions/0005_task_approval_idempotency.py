"""Prevent duplicate tasks from one approved email action."""
from typing import Sequence, Union

from alembic import op

revision: str = "0005_task_approval_idempotency"
down_revision: Union[str, Sequence[str], None] = "0004_thread_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.create_unique_constraint(
            "uq_tasks_user_email_title", ["user_id", "email_id", "title"]
        )


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint("uq_tasks_user_email_title", type_="unique")
