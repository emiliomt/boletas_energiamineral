"""SQLAlchemy engine/session wiring."""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
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


def _ensure_columns() -> None:
    """Lightweight, idempotent schema top-up for columns added after a table
    was first created. `Base.metadata.create_all` creates missing *tables*
    but never alters an existing one, so a deployed DB (e.g. Postgres) would
    otherwise be missing newly added columns and error on query. This adds any
    missing nullable columns via `ALTER TABLE ... ADD COLUMN`, which both
    SQLite and Postgres support. Kept intentionally minimal — this is not a
    general migration framework; it only backfills additive, nullable columns.
    """
    # table -> {column_name: SQL type}. All additive and nullable.
    expected: dict[str, dict[str, str]] = {
        "boleta_records": {
            "ocr_engine": "VARCHAR(64)",
            "proveedor": "VARCHAR(255)",
            "concesion_minera": "VARCHAR(255)",
            "representante_legal": "VARCHAR(255)",
        },
        "folio_batches": {
            "proveedor": "VARCHAR(255)",
            "destino": "VARCHAR(255)",
            "contrato": "VARCHAR(64)",
            "poder_calorifico_superior": "VARCHAR(64)",
            "humedad_pct": "VARCHAR(64)",
            "ceniza_pct": "VARCHAR(64)",
            "azufre_pct": "VARCHAR(64)",
            "fsi": "VARCHAR(64)",
            "granulometria": "VARCHAR(64)",
            "centro_explotacion": "VARCHAR(255)",
            "centro_acopio": "VARCHAR(255)",
            "concesion_minera": "VARCHAR(255)",
            "representante_legal": "VARCHAR(255)",
        },
    }
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, columns in expected.items():
            if table not in tables:
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, sql_type in columns.items():
                if name not in existing:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {sql_type}'))


def init_db() -> None:
    """Create all tables and (re)load the rule config CSVs. Idempotent —
    safe to call on every startup; config rows are upserted by natural key,
    so this never duplicates rows or touches processed boleta data."""
    settings.ensure_dirs()
    import app.models  # noqa: F401  (register models on Base.metadata)

    Base.metadata.create_all(bind=engine)
    _ensure_columns()

    from app.rules.config_loader import reload_all  # deferred: avoids a circular import

    db = SessionLocal()
    try:
        reload_all(db)
    finally:
        db.close()
