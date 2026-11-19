"""Store encrypted, owner-scoped LinkedIn OIDC connections."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011_linkedin_connections"
down_revision: Union[str, Sequence[str], None] = "0010_remove_assistant_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "linkedin_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("linkedin_sub", sa.String(length=255), nullable=False),
        sa.Column("access_token_enc", sa.Text(), nullable=False),
        sa.Column("refresh_token_enc", sa.Text(), nullable=False),
        sa.Column("access_token_expires_at", sa.DateTime(), nullable=True),
        sa.Column("refresh_token_expires_at", sa.DateTime(), nullable=True),
        sa.Column("scopes", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("given_name", sa.String(length=128), nullable=False),
        sa.Column("family_name", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("email_verified", sa.Boolean(), nullable=True),
        sa.Column("picture_url", sa.String(length=2048), nullable=False),
        sa.Column("locale", sa.String(length=64), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id"),
        sa.UniqueConstraint("linkedin_sub"),
    )
    op.create_index("ix_linkedin_connections_user_id", "linkedin_connections", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_linkedin_connections_user_id", table_name="linkedin_connections")
    op.drop_table("linkedin_connections")
