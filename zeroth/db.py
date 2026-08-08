from sqlalchemy import create_engine
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


def init_db() -> None:
    from zeroth import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
