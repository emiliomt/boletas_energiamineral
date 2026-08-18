from __future__ import annotations

from app.engines.classification import classify_trip


def test_exact_route_match_gives_full_confidence(db_session):
    result = classify_trip(db_session, "Mina San Jose", "Planta Norte")

    assert result.trip_type == "acarreo_carbon"
    assert result.confidence == 1.0
    assert result.matched_rule is not None
    assert result.matched_rule.route_id == "R001"
    assert result.exceptions == []


def test_fuzzy_route_match_when_ocr_noise_present(db_session):
    # Slight OCR noise/typo in both origin and destination.
    result = classify_trip(db_session, "Mina San Jse", "Planta Nrte")

    assert result.trip_type == "acarreo_carbon"
    assert 0.5 <= result.confidence < 1.0
    assert result.matched_rule is not None


def test_unknown_route_flags_exception(db_session):
    result = classify_trip(db_session, "Lugar Desconocido", "Otro Lugar")

    assert result.trip_type is None
    assert result.confidence == 0.0
    assert "unknown_route" in result.exceptions


def test_missing_origin_or_destination_flags_exception(db_session):
    result = classify_trip(db_session, None, "Planta Norte")

    assert "unknown_route" in result.exceptions
