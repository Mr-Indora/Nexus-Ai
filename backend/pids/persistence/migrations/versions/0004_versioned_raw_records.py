"""Allow one preserved raw record for each explicit source version."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_versioned_raw_records"
down_revision: Union[str, Sequence[str], None] = "3de1f4022121"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE pids_raw_records SET source_version = 'default-v1' "
            "WHERE source_version IS NULL"
        )
    )
    with op.batch_alter_table("pids_raw_records") as batch:
        batch.drop_constraint("uq_pids_raw_source_external", type_="unique")
        batch.create_unique_constraint(
            "uq_pids_raw_source_version",
            ["owner_id", "source_id", "external_id", "source_version"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text(
            "SELECT 1 FROM pids_raw_records "
            "GROUP BY owner_id, source_id, external_id HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicates:
        raise RuntimeError(
            "Cannot downgrade versioned PIDS source records while multiple versions exist"
        )
    with op.batch_alter_table("pids_raw_records") as batch:
        batch.drop_constraint("uq_pids_raw_source_version", type_="unique")
        batch.create_unique_constraint(
            "uq_pids_raw_source_external", ["owner_id", "source_id", "external_id"]
        )
