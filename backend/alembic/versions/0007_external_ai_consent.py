"""Require user consent before sending personal data to external AI."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007_external_ai_consent"
down_revision: Union[str, Sequence[str], None] = "0006_assistant_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("external_ai_consent", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "external_ai_consent")
