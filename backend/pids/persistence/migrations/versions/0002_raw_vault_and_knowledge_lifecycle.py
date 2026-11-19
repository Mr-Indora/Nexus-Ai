"""Add raw-vault metadata, evidence, and reviewed-knowledge lifecycle."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_raw_vault_lifecycle"
down_revision: Union[str, Sequence[str], None] = "0001_pids_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pids_raw_records", sa.Column("payload_size_bytes", sa.Integer(), nullable=True))
    op.add_column("pids_raw_records", sa.Column("content_type", sa.String(length=128), nullable=True))
    op.add_column(
        "pids_raw_records", sa.Column("character_encoding", sa.String(length=80), nullable=True)
    )
    op.add_column(
        "pids_raw_records", sa.Column("retention_expires_at", sa.DateTime(), nullable=True)
    )

    op.add_column(
        "pids_knowledge_records",
        sa.Column(
            "lifecycle_status",
            sa.String(length=16),
            nullable=False,
            server_default="candidate",
        )
    )
    op.add_column(
        "pids_knowledge_records", sa.Column("reviewed_at", sa.DateTime(), nullable=True)
    )
    op.add_column(
        "pids_knowledge_records", sa.Column("reviewer_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "pids_knowledge_records", sa.Column("review_decision", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "pids_knowledge_records", sa.Column("rejection_reason", sa.String(length=1024), nullable=True)
    )
    op.add_column(
        "pids_knowledge_records", sa.Column("superseded_by_id", sa.String(length=36), nullable=True)
    )
    op.add_column(
        "pids_knowledge_records",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index(
        "ix_pids_knowledge_owner_lifecycle",
        "pids_knowledge_records",
        ["owner_id", "lifecycle_status"],
    )

    op.create_table(
        "pids_evidence_references",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("knowledge_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("raw_record_id", sa.String(length=36), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("evidence_type", sa.String(length=32), nullable=False),
        sa.Column("offset_basis", sa.String(length=64), nullable=True),
        sa.Column("start_offset", sa.Integer(), nullable=True),
        sa.Column("end_offset", sa.Integer(), nullable=True),
        sa.Column("field_path", sa.String(length=512), nullable=True),
        sa.Column("extraction_method", sa.String(length=64), nullable=False),
        sa.Column("extraction_version", sa.String(length=128), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=False),
        sa.Column("transformation", sa.JSON(), nullable=False),
        sa.Column("excerpt", sa.String(length=512), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id", "knowledge_id"],
            ["pids_knowledge_records.owner_id", "pids_knowledge_records.id"],
            name="fk_pids_evidence_knowledge_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "source_id", "raw_record_id"],
            ["pids_raw_records.owner_id", "pids_raw_records.source_id", "pids_raw_records.id"],
            name="fk_pids_evidence_raw_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id",
            "knowledge_id",
            "idempotency_key",
            name="uq_pids_evidence_idempotency",
        ),
    )
    op.create_index(
        "ix_pids_evidence_raw",
        "pids_evidence_references",
        ["owner_id", "source_id", "raw_record_id"],
    )

    op.create_table(
        "pids_knowledge_review_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("knowledge_id", sa.String(length=36), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("from_state", sa.String(length=16), nullable=False),
        sa.Column("to_state", sa.String(length=16), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=64), nullable=False),
        sa.Column("rejection_reason", sa.String(length=1024), nullable=True),
        sa.Column("superseded_by_id", sa.String(length=36), nullable=True),
        sa.Column("version_before", sa.Integer(), nullable=False),
        sa.Column("version_after", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id", "knowledge_id"],
            ["pids_knowledge_records.owner_id", "pids_knowledge_records.id"],
            name="fk_pids_review_knowledge_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "superseded_by_id"],
            ["pids_knowledge_records.owner_id", "pids_knowledge_records.id"],
            name="fk_pids_review_successor_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "knowledge_id", "request_id", name="uq_pids_review_request"
        ),
    )
    op.create_index(
        "ix_pids_review_knowledge_version",
        "pids_knowledge_review_events",
        ["owner_id", "knowledge_id", "version_after"],
    )

    op.create_table(
        "pids_knowledge_conflicts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("existing_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("conflict_type", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=1024), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_id", "existing_id"],
            ["pids_knowledge_records.owner_id", "pids_knowledge_records.id"],
            name="fk_pids_conflict_existing_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "candidate_id"],
            ["pids_knowledge_records.owner_id", "pids_knowledge_records.id"],
            name="fk_pids_conflict_candidate_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id", "existing_id", "candidate_id",
            name="uq_pids_knowledge_conflict_pair",
        ),
    )
    op.create_index(
        "ix_pids_conflicts_candidate",
        "pids_knowledge_conflicts",
        ["owner_id", "candidate_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_pids_conflicts_candidate", table_name="pids_knowledge_conflicts")
    op.drop_table("pids_knowledge_conflicts")
    op.drop_index(
        "ix_pids_review_knowledge_version", table_name="pids_knowledge_review_events"
    )
    op.drop_table("pids_knowledge_review_events")
    op.drop_index("ix_pids_evidence_raw", table_name="pids_evidence_references")
    op.drop_table("pids_evidence_references")
    op.drop_index("ix_pids_knowledge_owner_lifecycle", table_name="pids_knowledge_records")
    for column in (
        "version",
        "superseded_by_id",
        "rejection_reason",
        "review_decision",
        "reviewer_id",
        "reviewed_at",
        "lifecycle_status",
    ):
        op.drop_column("pids_knowledge_records", column)
    for column in (
        "retention_expires_at",
        "character_encoding",
        "content_type",
        "payload_size_bytes",
    ):
        op.drop_column("pids_raw_records", column)
