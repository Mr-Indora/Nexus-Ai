"""Initialize or upgrade only the isolated PIDS development database."""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from config import settings
from pids.persistence.database import validate_isolated_database_url


def upgrade_isolated_database() -> str:
    database_url = validate_isolated_database_url(settings.PIDS_DATABASE_URL)
    config = Config("pids/persistence/alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, "head")
    return ScriptDirectory.from_config(config).get_current_head() or "unknown"


if __name__ == "__main__":
    revision = upgrade_isolated_database()
    print(f"Isolated PIDS database is at migration head {revision}.")
