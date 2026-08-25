#!/usr/bin/env python3
"""Create the DB schema (if needed) and load the sample config CSVs.

Usage: python scripts/init_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.rules.config_loader import reload_all  # noqa: E402


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        # Defensive backfill: init_db()'s additive migration already fills
        # `kind` via the column's DB-level DEFAULT, but this is a cheap,
        # idempotent safety net independent of that mechanism (e.g. a
        # column added out-of-band without a default).
        db.execute(text("UPDATE boleta_records SET kind = 'salida' WHERE kind IS NULL"))
        db.commit()
        counts = reload_all(db)
    finally:
        db.close()
    print("Database initialized.")
    for table, count in counts.items():
        print(f"  {table}: {count} rows loaded from CSV")


if __name__ == "__main__":
    main()
