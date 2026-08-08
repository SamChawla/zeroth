from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from zeroth.config import settings


def _pin_psycopg3(url: str) -> str:
    """Zerops injects DATABASE_URL from ${db_connectionString}, which is a bare
    postgresql:// URL. SQLAlchemy maps that scheme to the psycopg2 dialect, but
    requirements.txt ships psycopg 3 only - so name the driver explicitly rather
    than depending on the URL the platform happens to hand us."""
    for scheme in ("postgresql://", "postgres://"):
        if url.startswith(scheme):
            return "postgresql+psycopg://" + url[len(scheme) :]
    return url


engine = create_engine(_pin_psycopg3(settings.database_url), pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# create_all() only ever CREATEs missing tables - it will not add a column to a
# table that already exists, so columns introduced after the first deploy need
# stating here. Postgres' IF NOT EXISTS makes each one idempotent, which keeps
# this honest without pulling in a migration framework for a handful of columns.
_ADDED_COLUMNS = (
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS verify_target VARCHAR(20) DEFAULT ''",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS verified BOOLEAN DEFAULT FALSE",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS live_url VARCHAR(500) DEFAULT ''",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS kept_project_id VARCHAR(100) DEFAULT ''",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS compatibility JSON",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS provider VARCHAR(30) DEFAULT ''",
    "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS config_source VARCHAR(20) DEFAULT 'generated'",
)


def init_db() -> None:
    from zeroth import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        for statement in _ADDED_COLUMNS:
            conn.execute(text(statement))


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
