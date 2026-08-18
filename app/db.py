"""SQLAlchemy engine/session wiring."""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yields a session, closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create all tables and (re)load the rule config CSVs. Idempotent —
    safe to call on every startup; config rows are upserted by natural key,
    so this never duplicates rows or touches processed boleta data."""
    settings.ensure_dirs()
    import app.models  # noqa: F401  (register models on Base.metadata)

    Base.metadata.create_all(bind=engine)

    from app.rules.config_loader import reload_all  # deferred: avoids a circular import

    db = SessionLocal()
    try:
        reload_all(db)
    finally:
        db.close()
