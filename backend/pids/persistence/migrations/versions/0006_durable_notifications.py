"""Add per-user notification preferences and idempotent in-app deliveries."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_durable_notifications"
down_revision: Union[str, Sequence[str], None] = "0005_history_page_cursor"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pids_notification_preferences",
        sa.Column("owner_id", sa.Integer(), primary_key=True),
        sa.Column("daily_brief_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("daily_brief_time", sa.String(length=5), nullable=False, server_default="06:00"),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Kolkata"),
        sa.Column("deadline_reminders", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("high_priority_alerts", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "pids_notifications",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("delivery_key", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("owner_id", "delivery_key", name="uq_pids_notification_delivery"),
    )
    op.create_index("ix_pids_notifications_owner_created", "pids_notifications", ["owner_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_pids_notifications_owner_created", table_name="pids_notifications")
    op.drop_table("pids_notifications")
    op.drop_table("pids_notification_preferences")
