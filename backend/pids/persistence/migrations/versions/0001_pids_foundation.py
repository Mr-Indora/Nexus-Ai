"""Initial isolated PIDS persistence schema."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_pids_foundation"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pids_sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("account_ref", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "id", name="uq_pids_sources_owner_id"),
    )
    op.create_index("ix_pids_sources_owner_status", "pids_sources", ["owner_id", "status"])

    op.create_table(
        "pids_raw_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("external_id", sa.String(length=512), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("content_ref", sa.String(length=2048), nullable=True),
        sa.Column("source_timezone", sa.String(length=128), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(), nullable=False),
        sa.Column("source_version", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id", "source_id"],
            ["pids_sources.owner_id", "pids_sources.id"],
            name="fk_pids_raw_source_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "id", name="uq_pids_raw_owner_id"),
        sa.UniqueConstraint(
            "owner_id", "source_id", "id", name="uq_pids_raw_owner_source_id"
        ),
        sa.UniqueConstraint(
            "owner_id", "source_id", "external_id", name="uq_pids_raw_source_external"
        ),
    )
    op.create_index("ix_pids_raw_owner_hash", "pids_raw_records", ["owner_id", "content_sha256"])

    op.create_table(
        "pids_knowledge_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("record_type", sa.String(length=64), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("processing_version", sa.String(length=128), nullable=False),
        sa.Column("verification", sa.String(length=24), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "id", name="uq_pids_knowledge_owner_id"),
        sa.UniqueConstraint(
            "owner_id", "idempotency_key", name="uq_pids_knowledge_idempotency"
        ),
    )
    op.create_index(
        "ix_pids_knowledge_owner_type",
        "pids_knowledge_records",
        ["owner_id", "record_type"],
    )

    op.create_table(
        "pids_entities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("canonical_name", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "id", name="uq_pids_entities_owner_id"),
    )
    op.create_index("ix_pids_entities_owner_type", "pids_entities", ["owner_id", "entity_type"])

    op.create_table(
        "pids_entity_aliases",
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("alias", sa.String(length=512), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id", "entity_id"],
            ["pids_entities.owner_id", "pids_entities.id"],
            name="fk_pids_entity_alias_owner",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("owner_id", "entity_id", "alias"),
    )
    op.create_index("ix_pids_entity_aliases_owner_alias", "pids_entity_aliases", ["owner_id", "alias"])

    op.create_table(
        "pids_knowledge_sources",
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("knowledge_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("raw_record_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_location", sa.String(length=512), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_id", "knowledge_id"],
            ["pids_knowledge_records.owner_id", "pids_knowledge_records.id"],
            name="fk_pids_knowledge_source_knowledge_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "source_id", "raw_record_id"],
            ["pids_raw_records.owner_id", "pids_raw_records.source_id", "pids_raw_records.id"],
            name="fk_pids_knowledge_source_raw_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("owner_id", "knowledge_id", "source_id", "raw_record_id"),
    )
    op.create_index(
        "ix_pids_knowledge_sources_raw",
        "pids_knowledge_sources",
        ["owner_id", "source_id", "raw_record_id"],
    )

    op.create_table(
        "pids_knowledge_entities",
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("knowledge_id", sa.String(length=36), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id", "knowledge_id"],
            ["pids_knowledge_records.owner_id", "pids_knowledge_records.id"],
            name="fk_pids_knowledge_entity_knowledge_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "entity_id"],
            ["pids_entities.owner_id", "pids_entities.id"],
            name="fk_pids_knowledge_entity_entity_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("owner_id", "knowledge_id", "entity_id"),
    )
    op.create_index(
        "ix_pids_knowledge_entities_entity",
        "pids_knowledge_entities",
        ["owner_id", "entity_id"],
    )

    op.create_table(
        "pids_entity_evidence",
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("raw_record_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_location", sa.String(length=512), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_id", "entity_id"],
            ["pids_entities.owner_id", "pids_entities.id"],
            name="fk_pids_entity_evidence_entity_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "source_id", "raw_record_id"],
            ["pids_raw_records.owner_id", "pids_raw_records.source_id", "pids_raw_records.id"],
            name="fk_pids_entity_evidence_raw_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("owner_id", "entity_id", "source_id", "raw_record_id"),
    )
    op.create_index(
        "ix_pids_entity_evidence_raw",
        "pids_entity_evidence",
        ["owner_id", "source_id", "raw_record_id"],
    )

    op.create_table(
        "pids_relationships",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.String(length=36), nullable=False),
        sa.Column("object_id", sa.String(length=36), nullable=False),
        sa.Column("relation_type", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id", "subject_id"],
            ["pids_entities.owner_id", "pids_entities.id"],
            name="fk_pids_relationship_subject_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "object_id"],
            ["pids_entities.owner_id", "pids_entities.id"],
            name="fk_pids_relationship_object_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "id", name="uq_pids_relationships_owner_id"),
    )
    op.create_index("ix_pids_relationships_subject", "pids_relationships", ["owner_id", "subject_id"])
    op.create_index("ix_pids_relationships_object", "pids_relationships", ["owner_id", "object_id"])

    op.create_table(
        "pids_relationship_evidence",
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("relationship_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("raw_record_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_location", sa.String(length=512), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_id", "relationship_id"],
            ["pids_relationships.owner_id", "pids_relationships.id"],
            name="fk_pids_relationship_evidence_relationship_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "source_id", "raw_record_id"],
            ["pids_raw_records.owner_id", "pids_raw_records.source_id", "pids_raw_records.id"],
            name="fk_pids_relationship_evidence_raw_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "owner_id", "relationship_id", "source_id", "raw_record_id"
        ),
    )
    op.create_index(
        "ix_pids_relationship_evidence_raw",
        "pids_relationship_evidence",
        ["owner_id", "source_id", "raw_record_id"],
    )

    op.create_table(
        "pids_deadlines",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("record_id", sa.String(length=36), nullable=False),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("source_timezone", sa.String(length=128), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id", "record_id"],
            ["pids_knowledge_records.owner_id", "pids_knowledge_records.id"],
            name="fk_pids_deadline_knowledge_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "id", name="uq_pids_deadlines_owner_id"),
    )
    op.create_index("ix_pids_deadlines_owner_record", "pids_deadlines", ["owner_id", "record_id"])

    op.create_table(
        "pids_deadline_evidence",
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("deadline_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("raw_record_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_location", sa.String(length=512), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_id", "deadline_id"],
            ["pids_deadlines.owner_id", "pids_deadlines.id"],
            name="fk_pids_deadline_evidence_deadline_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "source_id", "raw_record_id"],
            ["pids_raw_records.owner_id", "pids_raw_records.source_id", "pids_raw_records.id"],
            name="fk_pids_deadline_evidence_raw_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("owner_id", "deadline_id", "source_id", "raw_record_id"),
    )
    op.create_index(
        "ix_pids_deadline_evidence_raw",
        "pids_deadline_evidence",
        ["owner_id", "source_id", "raw_record_id"],
    )

    op.create_table(
        "pids_feedback",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pids_feedback_owner_target",
        "pids_feedback",
        ["owner_id", "target_type", "target_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_pids_feedback_owner_target", table_name="pids_feedback")
    op.drop_table("pids_feedback")
    op.drop_index("ix_pids_deadline_evidence_raw", table_name="pids_deadline_evidence")
    op.drop_table("pids_deadline_evidence")
    op.drop_index("ix_pids_deadlines_owner_record", table_name="pids_deadlines")
    op.drop_table("pids_deadlines")
    op.drop_index(
        "ix_pids_relationship_evidence_raw", table_name="pids_relationship_evidence"
    )
    op.drop_table("pids_relationship_evidence")
    op.drop_index("ix_pids_relationships_object", table_name="pids_relationships")
    op.drop_index("ix_pids_relationships_subject", table_name="pids_relationships")
    op.drop_table("pids_relationships")
    op.drop_index("ix_pids_entity_evidence_raw", table_name="pids_entity_evidence")
    op.drop_table("pids_entity_evidence")
    op.drop_index(
        "ix_pids_knowledge_entities_entity", table_name="pids_knowledge_entities"
    )
    op.drop_table("pids_knowledge_entities")
    op.drop_index("ix_pids_entity_aliases_owner_alias", table_name="pids_entity_aliases")
    op.drop_table("pids_entity_aliases")
    op.drop_index("ix_pids_knowledge_sources_raw", table_name="pids_knowledge_sources")
    op.drop_table("pids_knowledge_sources")
    op.drop_index("ix_pids_entities_owner_type", table_name="pids_entities")
    op.drop_table("pids_entities")
    op.drop_index("ix_pids_knowledge_owner_type", table_name="pids_knowledge_records")
    op.drop_table("pids_knowledge_records")
    op.drop_index("ix_pids_raw_owner_hash", table_name="pids_raw_records")
    op.drop_table("pids_raw_records")
    op.drop_index("ix_pids_sources_owner_status", table_name="pids_sources")
    op.drop_table("pids_sources")
