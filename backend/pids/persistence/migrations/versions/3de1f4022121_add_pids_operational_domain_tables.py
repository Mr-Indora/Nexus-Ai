"""add PIDS operational domain tables

Revision ID: 3de1f4022121
Revises: 0002_raw_vault_lifecycle
Create Date: 2026-11-12 22:54:53.443365
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from pids.persistence.models import Base

revision: str = '3de1f4022121'
down_revision: Union[str, Sequence[str], None] = '0002_raw_vault_lifecycle'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    for name in (
        "pids_source_sync_state",
        "pids_email_messages",
        "pids_thread_intelligence",
        "pids_task_candidates",
        "pids_event_candidates",
        "pids_memory_items",
        "pids_insights",
        "pids_recommendations",
        "pids_action_approvals",
        "pids_processing_jobs",
        "pids_audit_events",
    ):
        Base.metadata.tables[name].create(bind=bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for name in (
        "pids_audit_events",
        "pids_processing_jobs",
        "pids_action_approvals",
        "pids_recommendations",
        "pids_insights",
        "pids_memory_items",
        "pids_event_candidates",
        "pids_task_candidates",
        "pids_thread_intelligence",
        "pids_email_messages",
        "pids_source_sync_state",
    ):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=False)
