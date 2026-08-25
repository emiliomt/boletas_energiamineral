from __future__ import annotations

from app.engines.classification import classify_entrada, classify_trip
from app.models import Producer


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


# --- classify_entrada (Phase 2) --------------------------------------------


def test_classify_entrada_with_known_producer_default_origin_resolves(db_session):
    # Real placeholder data: Producer "Bradfort" has default_origin
    # "Bradfort", matched against route_config.csv's R006.
    producer = db_session.query(Producer).filter_by(name="Bradfort").one()

    result = classify_entrada(db_session, producer)

    assert result.trip_type == "recepcion_compra"
    assert result.confidence == 1.0
    assert result.matched_rule is not None
    assert result.matched_rule.route_id == "R006"
    assert result.exceptions == []


def test_classify_entrada_none_producer_flags_unknown_producer(db_session):
    result = classify_entrada(db_session, None)

    assert result.trip_type is None
    assert "unknown_producer" in result.exceptions


def test_classify_entrada_inactive_producer_flags_unknown_producer(db_session):
    producer = Producer(name="TEST Inactive Producer", default_origin="Bradfort", active=False)
    db_session.add(producer)
    db_session.flush()

    result = classify_entrada(db_session, producer)

    assert "unknown_producer" in result.exceptions


def test_classify_entrada_producer_without_default_origin_flags_unknown_producer(db_session):
    producer = Producer(name="TEST No Origin Producer", default_origin=None, active=True)
    db_session.add(producer)
    db_session.flush()

    result = classify_entrada(db_session, producer)

    assert "unknown_producer" in result.exceptions


def test_classify_entrada_no_matching_route_flags_unknown_route(db_session):
    producer = Producer(name="TEST Unrouted Producer", default_origin="Lugar Sin Ruta", active=True)
    db_session.add(producer)
    db_session.flush()

    result = classify_entrada(db_session, producer)

    assert result.trip_type is None
    assert "unknown_route" in result.exceptions


def test_classify_entrada_ignores_destination_entirely(db_session):
    # Fuzzy-typo'd default_origin still resolves via origin-only matching --
    # no destination is ever considered.
    producer = Producer(name="TEST Fuzzy Producer", default_origin="Bradfrt", active=True)
    db_session.add(producer)
    db_session.flush()

    result = classify_entrada(db_session, producer)

    assert result.trip_type == "recepcion_compra"
    assert 0.5 <= result.confidence < 1.0
