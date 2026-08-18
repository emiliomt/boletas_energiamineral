"""End-to-end smoke test against the real Tesseract binary and the actual
generated fixture images (as opposed to test_pipeline_end_to_end.py, which
uses FakeOCRAdapter for fully deterministic unit-level coverage). Skipped
if either the fixtures or the tesseract binary aren't available — run
`python scripts/generate_sample_fixtures.py` first."""
from __future__ import annotations

import json
import shutil

import pytest

from app.models import Batch, Boleta
from app.ocr.tesseract_adapter import TesseractOCRAdapter
from app.pipeline.orchestrator import process_boleta
from tests.conftest import BOLETAS_FIXTURES_DIR, EXPECTED_OUTPUTS_DIR

pytestmark = pytest.mark.skipif(
    shutil.which("tesseract") is None or not BOLETAS_FIXTURES_DIR.exists(),
    reason="tesseract binary or generated fixtures not available",
)


def _process_fixture(db_session, filename: str):
    batch = Batch(label="smoke-test")
    db_session.add(batch)
    db_session.flush()
    boleta = Boleta(
        batch_id=batch.id,
        original_filename=filename,
        stored_path=str(BOLETAS_FIXTURES_DIR / filename),
        mime_type="image/png",
        page_number=1,
        sha256_hash="n/a",
    )
    db_session.add(boleta)
    db_session.flush()
    return process_boleta(db_session, boleta, TesseractOCRAdapter())


@pytest.mark.parametrize(
    "filename,expected_file",
    [
        ("sample_boleta_01.png", "sample_boleta_01.json"),
        ("sample_boleta_02.png", "sample_boleta_02.json"),
    ],
)
def test_clean_fixture_matches_expected_output(db_session, filename, expected_file):
    expected = json.loads((EXPECTED_OUTPUTS_DIR / expected_file).read_text())

    record = _process_fixture(db_session, filename)

    assert record.folio == expected["boleta_id"]
    assert record.date == expected["date"]
    assert record.origin == expected["origin"]
    assert record.destination == expected["destination"]
    assert record.trip_type == expected["trip_type"]
    assert record.weight == expected["weight"]
    assert record.weight_source == expected["weight_source"]
    assert record.tariff_amount == expected["tariff_amount"]
    assert record.inventory_direction == expected["inventory_direction"]
    assert record.status == expected["status"]
    assert record.exceptions == expected["exceptions"]


def test_illegible_fixture_goes_to_needs_review(db_session):
    expected = json.loads((EXPECTED_OUTPUTS_DIR / "sample_boleta_03_illegible.json").read_text())

    record = _process_fixture(db_session, "sample_boleta_03_illegible.png")

    assert record.folio == expected["boleta_id"]
    assert record.status == expected["status"]
    for code in expected["exceptions_include"]:
        assert code in record.exceptions
