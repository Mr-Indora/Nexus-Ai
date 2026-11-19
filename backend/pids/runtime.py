"""Access the optional PIDS SQLite store without applying migrations at startup."""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from config import settings
from pids.persistence.database import create_pids_engine, create_session_factory


@lru_cache(maxsize=1)
def _runtime_engine():
    return create_pids_engine(settings.PIDS_DATABASE_URL)


def reset_runtime_engine() -> None:
    """Dispose cached connections, primarily for clean local database swaps/tests."""
    if _runtime_engine.cache_info().currsize:
        _runtime_engine().dispose()
    _runtime_engine.cache_clear()


@contextmanager
def pids_session() -> Iterator[Session]:
    """Yield a session only when an existing, current isolated DB is available."""
    if not settings.PIDS_ENABLED:
        raise RuntimeError("PIDS is disabled; set PIDS_ENABLED=true after isolated setup")
    url = make_url(settings.PIDS_DATABASE_URL)
    database_path = Path(url.database or "")
    if not database_path.is_file():
        raise RuntimeError("PIDS database is not initialized; run the isolated PIDS migration command")

    engine = _runtime_engine()
    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        if "pids_alembic_version" not in tables:
            raise RuntimeError("PIDS database migration state is missing")
        current = connection.execute(
            text("SELECT version_num FROM pids_alembic_version")
        ).scalar_one_or_none()
        migration_config = Config(
            str(Path(__file__).resolve().parent / "persistence" / "alembic.ini")
        )
        expected = ScriptDirectory.from_config(migration_config).get_current_head()
        # 0006 only adds notification tables. Keep the rest of PIDS readable and
        # syncable until the operator takes the pre-upgrade backup and applies it.
        # New notification endpoints separately require those physical tables.
        backwards_compatible = current == "0005_history_page_cursor" and expected == "0006_durable_notifications"
        if current != expected and not backwards_compatible:
            raise RuntimeError("PIDS database migration revision does not match the application")

    session = create_session_factory(engine)()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
