#!/usr/bin/env python3
"""Re-import the rule config CSVs into the DB without touching other data.

Run this after an admin edits any of the CSVs in app/rules/.
Usage: python scripts/load_config.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, init_db  # noqa: E402
from app.rules.config_loader import reload_all  # noqa: E402


def main() -> None:
    init_db()  # no-op if tables already exist
    db = SessionLocal()
    try:
        counts = reload_all(db)
    finally:
        db.close()
    print("Config reloaded from CSV:")
    for table, count in counts.items():
        print(f"  {table}: {count} rows")


if __name__ == "__main__":
    main()
