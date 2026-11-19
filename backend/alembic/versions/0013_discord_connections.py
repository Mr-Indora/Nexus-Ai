"""Add encrypted Discord identity connection."""
from alembic import op
import sqlalchemy as sa

revision: str = "0013_discord_connections"
down_revision: str = "0012_linkedin_profile_headline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discord_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("discord_id", sa.String(length=32), nullable=False),
        sa.Column("access_token_enc", sa.Text(), nullable=False, server_default=""),
        sa.Column("refresh_token_enc", sa.Text(), nullable=False, server_default=""),
        sa.Column("access_token_expires_at", sa.DateTime(), nullable=True),
        sa.Column("scopes", sa.String(length=128), nullable=False, server_default="identify"),
        sa.Column("username", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("global_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("avatar_url", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("discord_id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_discord_connections_user_id", "discord_connections", ["user_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_discord_connections_user_id", table_name="discord_connections")
    op.drop_table("discord_connections")
