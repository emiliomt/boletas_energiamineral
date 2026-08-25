"""Tests for app.db._ensure_columns -- the additive-only column migration
that lets an existing (pre-Phase-1) database gain the new `kind`/
`producer_id` columns on `boleta_records` without any data loss, and lets
scripts/init_db.py's defensive backfill fill any straggling nulls."""
from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

import app.db as app_db
from app.db import Base


def _make_pre_phase1_engine():
    """A fresh in-memory DB with boleta_records in its pre-Phase-1 shape --
    every column BoletaRecord had *before* this phase (i.e. everything
    except `kind`/`producer_id`), none of the new Phase 1 tables, plus one
    pre-existing row -- simulating an existing installation being upgraded.
    (Must include every pre-existing column, not just a handful: otherwise
    _ensure_columns would see unrelated columns as "missing" too and try to
    ALTER them in, which isn't what this test is exercising.)"""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE boleta_records ("
                "id INTEGER PRIMARY KEY, "
                "boleta_id INTEGER, "
                "ocr_text TEXT, "
                "ocr_confidence REAL, "
                "folio VARCHAR(128), "
                "date VARCHAR(32), "
                "origin VARCHAR(255), "
                "secondary_origin VARCHAR(255), "
                "destination VARCHAR(255), "
                "contract_number VARCHAR(64), "
                "material VARCHAR(255), "
                "fletero VARCHAR(255), "
                "truck_box_number VARCHAR(64), "
                "weight REAL, "
                "weight_declared REAL, "
                "weight_source VARCHAR(16), "
                "quality_data JSON, "
                "trip_type VARCHAR(128), "
                "tariff_amount REAL, "
                "inventory_direction VARCHAR(16), "
                "inventory_quantity REAL, "
                "confidence_score REAL, "
                "status VARCHAR(16), "
                "exceptions JSON, "
                "field_confidences JSON, "
                "matched_route_rule_id INTEGER, "
                "matched_tariff_rule_id INTEGER, "
                "matched_weight_rule_id INTEGER, "
                "created_at DATETIME, "
                "updated_at DATETIME"
                ")"
            )
        )
        conn.execute(
            text(
                "INSERT INTO boleta_records (id, boleta_id, folio, origin, status) "
                "VALUES (1, 1, 'B-0001', 'Mina San Jose', 'auto_processed')"
            )
        )
    return engine


def test_ensure_columns_backfills_kind_and_adds_producer_id_without_data_loss(monkeypatch):
    engine = _make_pre_phase1_engine()
    monkeypatch.setattr(app_db, "engine", engine)

    Base.metadata.create_all(bind=engine)
    app_db._ensure_columns("boleta_records")

    inspector = inspect(engine)
    cols = {c["name"] for c in inspector.get_columns("boleta_records")}
    assert "kind" in cols
    assert "producer_id" in cols

    tables = set(inspector.get_table_names())
    assert {"producers", "transportistas", "transportista_aliases", "pricing_rules"} <= tables

    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = session_local()
    try:
        row = db.execute(text("SELECT id, boleta_id, folio, origin, status, kind, producer_id FROM boleta_records WHERE id = 1")).one()
    finally:
        db.close()
    assert row.boleta_id == 1
    assert row.folio == "B-0001"
    assert row.origin == "Mina San Jose"
    assert row.status == "auto_processed"
    assert row.kind == "salida"
    assert row.producer_id is None


def test_ensure_columns_is_idempotent_on_rerun(monkeypatch):
    engine = _make_pre_phase1_engine()
    monkeypatch.setattr(app_db, "engine", engine)

    Base.metadata.create_all(bind=engine)
    app_db._ensure_columns("boleta_records")
    app_db._ensure_columns("boleta_records")  # must not raise (duplicate ADD COLUMN)

    inspector = inspect(engine)
    cols = [c["name"] for c in inspector.get_columns("boleta_records")]
    assert cols.count("kind") == 1
    assert cols.count("producer_id") == 1


def test_defensive_update_statement_fills_any_null_kind():
    """Simulates a `kind` column added without a DB-level default (e.g. an
    out-of-band ALTER), independent of the DEFAULT-clause mechanism
    _ensure_columns normally relies on -- exercises the same UPDATE
    statement scripts/init_db.py runs as a safety net."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE boleta_records (id INTEGER PRIMARY KEY, folio VARCHAR(128), kind VARCHAR(16))")
        )
        conn.execute(text("INSERT INTO boleta_records (id, folio, kind) VALUES (1, 'B-0001', NULL)"))
        conn.execute(text("INSERT INTO boleta_records (id, folio, kind) VALUES (2, 'B-0002', 'entrada')"))

    with engine.begin() as conn:
        conn.execute(text("UPDATE boleta_records SET kind = 'salida' WHERE kind IS NULL"))

    with engine.begin() as conn:
        remaining_nulls = conn.execute(text("SELECT COUNT(*) FROM boleta_records WHERE kind IS NULL")).scalar()
        kinds = {r[0]: r[1] for r in conn.execute(text("SELECT id, kind FROM boleta_records")).all()}

    assert remaining_nulls == 0
    assert kinds[1] == "salida"  # backfilled
    assert kinds[2] == "entrada"  # untouched, already had a value
