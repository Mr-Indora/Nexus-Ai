"""Explicit-path SQLite engine helpers for isolated PIDS persistence."""

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ISOLATED_WORKSPACE = PROJECT_ROOT / "pids-isolated-work"


def validate_isolated_database_url(database_url: str) -> str:
    url = make_url(database_url)
    if url.drivername not in {"sqlite", "sqlite+pysqlite"} or not url.database:
        raise ValueError("PIDS persistence requires an explicit file-backed SQLite URL")
    if url.database == ":memory:":
        raise ValueError("in-memory databases are not allowed for PIDS persistence")

    database_path = Path(url.database)
    if not database_path.is_absolute():
        raise ValueError("PIDS SQLite database path must be absolute")
    try:
        database_path.resolve().relative_to(ISOLATED_WORKSPACE.resolve())
    except ValueError as exc:
        raise ValueError("PIDS SQLite database must be inside pids-isolated-work") from exc
    return url.render_as_string(hide_password=False)


def create_pids_engine(database_url: str):
    """Create an engine only for an explicitly named isolated SQLite file."""
    validated_url = validate_isolated_database_url(database_url)
    ISOLATED_WORKSPACE.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        validated_url,
        # Sync and UI actions can touch the isolated store at the same time.
        # Give SQLite a reasonable lock wait instead of failing immediately
        # with ``database is locked`` during a short competing write.
        connect_args={"check_same_thread": False, "timeout": 20},
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=20000")
        finally:
            cursor.close()

    return engine


def create_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def begin_nested_transaction(session):
    """Keep SQLite savepoints nested under a real caller-controlled transaction."""
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        driver_connection = connection.connection.driver_connection
        if not driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")
    return session.begin_nested()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
