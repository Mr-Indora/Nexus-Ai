from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from config import settings

_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(
    settings.DATABASE_URL,
    connect_args=_args,
    pool_pre_ping=True,
)
if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from models import models  # noqa: F401  (register tables)

    check_database()


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def check_database() -> None:
    """Verify connectivity and that the database is at the source migration head."""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        if engine.dialect.name == "sqlite":
            tables = set(inspect(connection).get_table_names())
            if "alembic_version" not in tables:
                raise RuntimeError("Database has no Alembic revision")
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
            alembic_config = Config(str(Path(__file__).resolve().parent / "alembic.ini"))
            expected = ScriptDirectory.from_config(alembic_config).get_current_head()
            # Existing local databases stay readable before explicit migrations;
            # provider-specific endpoints report setup-needed until their tables exist.
            supported_pre_update = (
                revision in {
                    "0008_source_sync_freshness",
                    "0009_task_status_events",
                    "0010_remove_assistant_history",
                    "0011_linkedin_connections",
                    "0012_linkedin_profile_headline",
                }
                and expected == "0014_remove_discord_connection"
            )
            if revision != expected and not supported_pre_update:
                raise RuntimeError("Database migration revision does not match the application")
