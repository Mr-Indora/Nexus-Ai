"""Retain Gmail thread identifiers and bounded thread intelligence."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_thread_intelligence"
down_revision: Union[str, Sequence[str], None] = "0003_intelligence_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("emails", sa.Column("thread_id", sa.String(128), nullable=True))
    op.create_index("ix_emails_thread_id", "emails", ["thread_id"])
    op.create_table(
        "thread_intelligence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("thread_id", sa.String(128), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("decisions", sa.Text(), nullable=False),
        sa.Column("commitments", sa.Text(), nullable=False),
        sa.Column("deadlines", sa.Text(), nullable=False),
        sa.Column("pending_response", sa.Boolean(), nullable=False),
        sa.Column("source_email_ids", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "thread_id"),
    )
    op.create_index("ix_thread_intelligence_user_id", "thread_intelligence", ["user_id"])


def downgrade() -> None:
    op.drop_table("thread_intelligence")
    op.drop_index("ix_emails_thread_id", table_name="emails")
    op.drop_column("emails", "thread_id")
