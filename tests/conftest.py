from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.rules.config_loader import reload_all

FIXTURES_DIR = Path(__file__).parent / "fixtures"
BOLETAS_FIXTURES_DIR = FIXTURES_DIR / "boletas"
EXPECTED_OUTPUTS_DIR = FIXTURES_DIR / "expected_outputs"


@pytest.fixture()
def db_session():
    """In-memory SQLite DB, schema created and sample config loaded fresh per test."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = session_local()
    reload_all(db)
    try:
        yield db
    finally:
        db.close()
