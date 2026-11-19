"""Store opaque Gmail History pagination cursors without truncation."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_history_page_cursor"
down_revision: Union[str, Sequence[str], None] = "0004_versioned_raw_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("pids_source_sync_state") as batch:
        batch.alter_column(
            "cursor",
            existing_type=sa.String(length=2048),
            type_=sa.Text(),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("pids_source_sync_state") as batch:
        batch.alter_column(
            "cursor",
            existing_type=sa.Text(),
            type_=sa.String(length=2048),
            existing_nullable=True,
        )
