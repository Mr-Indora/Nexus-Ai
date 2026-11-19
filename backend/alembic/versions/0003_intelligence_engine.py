"""Add memory lifecycle and email intelligence records."""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_intelligence_engine"
down_revision: Union[str, Sequence[str], None] = "0002_user_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_memories", sa.Column("category", sa.String(32), nullable=False, server_default="preference"))
    op.add_column("user_memories", sa.Column("source", sa.String(64), nullable=False, server_default="user"))
    op.add_column("user_memories", sa.Column("approval_status", sa.String(16), nullable=False, server_default="approved"))
    op.add_column("user_memories", sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table(
        "memory_candidates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("confidence", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_memory_candidates_user_id", "memory_candidates", ["user_id"])
    op.create_table(
        "email_intelligence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("email_id", sa.Integer(), sa.ForeignKey("emails.id"), nullable=False),
        sa.Column("topic", sa.String(255), nullable=False),
        sa.Column("facts", sa.Text(), nullable=False),
        sa.Column("decisions", sa.Text(), nullable=False),
        sa.Column("commitments", sa.Text(), nullable=False),
        sa.Column("response_required", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "email_id"),
    )
    op.create_index("ix_email_intelligence_user_id", "email_intelligence", ["user_id"])
    op.create_table(
        "action_suggestions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("email_id", sa.Integer(), sa.ForeignKey("emails.id"), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("responsible", sa.String(255), nullable=False),
        sa.Column("deadline", sa.DateTime(), nullable=True),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("confidence", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "email_id", "title"),
    )
    op.create_index("ix_action_suggestions_user_id", "action_suggestions", ["user_id"])


def downgrade() -> None:
    op.drop_table("action_suggestions")
    op.drop_table("email_intelligence")
    op.drop_index("ix_memory_candidates_user_id", table_name="memory_candidates")
    op.drop_table("memory_candidates")
    op.drop_column("user_memories", "active")
    op.drop_column("user_memories", "approval_status")
    op.drop_column("user_memories", "source")
    op.drop_column("user_memories", "category")
