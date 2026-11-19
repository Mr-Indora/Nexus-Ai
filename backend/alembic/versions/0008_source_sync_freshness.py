"""Track Gmail and Calendar sync freshness independently."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008_source_sync_freshness"
down_revision: Union[str, Sequence[str], None] = "0007_external_ai_consent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("gmail_last_synced_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("calendar_last_synced_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "calendar_last_synced_at")
    op.drop_column("users", "gmail_last_synced_at")
