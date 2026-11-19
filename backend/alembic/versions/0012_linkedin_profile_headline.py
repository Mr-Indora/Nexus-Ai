"""Store the LinkedIn OIDC headline claim when LinkedIn returns it."""
from alembic import op
import sqlalchemy as sa

revision: str = "0012_linkedin_profile_headline"
down_revision: str = "0011_linkedin_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "linkedin_connections",
        sa.Column("headline", sa.String(length=512), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("linkedin_connections", "headline")
