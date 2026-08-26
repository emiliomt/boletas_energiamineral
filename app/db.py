"""SQLAlchemy engine/session wiring."""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
if settings.database_url.startswith("sqlite"):
    # Match Postgres: SQLite ignores FOREIGN KEY clauses unless this PRAGMA
    # is set on every connection (it is not a persistent file setting).
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
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


def _ensure_columns(table_name: str) -> None:
    """Additive-only column migration for a table that may already exist
    from before this column was added to the model. Base.metadata.create_all()
    only creates missing TABLES, not missing COLUMNS on tables that already
    exist -- this fills that gap for in-place upgrades of an existing DB.
    New tables need no entry here; create_all() creates those in full.
    Derives each column's type/default/nullability straight from the ORM
    model (rather than a hand-maintained per-table dict) so it stays correct
    as columns are added, and can express a NOT NULL column with a DEFAULT
    (e.g. BoletaRecord.kind), not just plain nullable ones.
    Not a general migration framework (no down-migrations, no type changes,
    no drops) -- if schema evolution needs grow past simple additive
    columns, adopt Alembic.
    """
    import app.models  # noqa: F401  (register models on Base.metadata)

    inspector = inspect(engine)
    if table_name not in inspector.get_table_names():
        return  # brand-new table -- create_all() already created it in full
    table = Base.metadata.tables[table_name]
    existing_cols = {c["name"] for c in inspector.get_columns(table_name)}
    with engine.begin() as conn:
        for column in table.columns:
            if column.name in existing_cols:
                continue
            col_type = column.type.compile(dialect=engine.dialect)
            ddl = f"ALTER TABLE {table_name} ADD COLUMN {column.name} {col_type}"
            if column.default is not None and getattr(column.default, "is_scalar", False):
                default_value = column.default.arg
                literal = f"'{default_value}'" if isinstance(default_value, str) else str(default_value)
                ddl += f" DEFAULT {literal}"
            if not column.nullable:
                ddl += " NOT NULL"
            conn.execute(text(ddl))


def init_db() -> None:
    """Create all tables and (re)load the rule config CSVs. Idempotent —
    safe to call on every startup; config rows are upserted by natural key,
    so this never duplicates rows or touches processed boleta data."""
    settings.ensure_dirs()
    import app.models  # noqa: F401  (register models on Base.metadata)

    Base.metadata.create_all(bind=engine)  # creates producers, transportistas, transportista_aliases, pricing_rules
    _ensure_columns(
        "boleta_records"
    )  # kind, producer_id, ocr_engine, proveedor, concesion_minera, representante_legal,
    # salida_status, cfe_entry_weight, cfe_exit_weight, delivered_weight, reconciled_with_record_id
    _ensure_columns("folio_batches")  # batch-level pre-printed fields (proveedor, destino, contrato, quality spec, ...)
    _ensure_columns("batches")  # kind, producer_id (Phase 2: Entrada pipeline)
    _ensure_columns("boletas")  # document_type (Phase 3: Salida two-document reconciliation)
    _ensure_columns("producers")  # precio_caja_carbon, precio_transporte (Proveedores admin)

    from app.rules.config_loader import reload_all  # deferred: avoids a circular import

    db = SessionLocal()
    try:
        reload_all(db)
    finally:
        db.close()
