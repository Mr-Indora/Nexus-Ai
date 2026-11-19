from pathlib import Path
import sys

from alembic import context
backend_path = str(Path(__file__).resolve().parents[3])
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from pids.persistence.database import create_pids_engine, validate_isolated_database_url
from pids.persistence.models import Base

config = context.config
database_url = validate_isolated_database_url(config.get_main_option("sqlalchemy.url"))
target_metadata = Base.metadata
version_table = "pids_alembic_version"


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=version_table,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_pids_engine(database_url)
    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                version_table=version_table,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
