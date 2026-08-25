from __future__ import annotations

from app.engines.tariff import compute_entrada_tariff, compute_salida_tariff, compute_tariff
from app.models import Producer


def test_known_trip_type_and_distance_band_resolves_tariff(db_session):
    result = compute_tariff(db_session, "acarreo_carbon", "0-10km")

    assert result.tariff_amount == 850.00
    assert result.matched_rule is not None
    assert result.exceptions == []


def test_any_band_tariff_used_when_distance_band_matches_any(db_session):
    result = compute_tariff(db_session, "recepcion_compra", "any")

    assert result.tariff_amount == 0.00
    assert result.exceptions == []


def test_unknown_trip_type_flags_exception(db_session):
    result = compute_tariff(db_session, "trip_type_nunca_visto", "0-10km")

    assert result.tariff_amount is None
    assert "unknown_tariff" in result.exceptions


def test_known_trip_type_unknown_distance_band_flags_exception(db_session):
    result = compute_tariff(db_session, "acarreo_carbon", "999-1000km")

    assert result.tariff_amount is None
    assert "unknown_tariff" in result.exceptions


def test_missing_trip_type_flags_exception(db_session):
    result = compute_tariff(db_session, None, "0-10km")

    assert "unknown_tariff" in result.exceptions


# --- compute_entrada_tariff (Phase 2) --------------------------------------


def test_compute_entrada_tariff_flat_mode_ignores_weight(db_session):
    # Real placeholder data: PricingRule P002 (Bradfort, flat, 900.00) is the
    # currently-effective rule -- P001 (also Bradfort) is superseded/past.
    producer = db_session.query(Producer).filter_by(name="Bradfort").one()

    result = compute_entrada_tariff(db_session, producer, weight=None)

    assert result.tariff_amount == 900.00
    assert result.matched_rule is not None
    assert result.matched_rule.pricing_mode == "flat"
    assert result.exceptions == []


def test_compute_entrada_tariff_per_weight_mode_with_weight_multiplies_rate(db_session):
    # Real placeholder data: PricingRule P003 (CTU/MINSA, per_weight, 120.00/ton).
    producer = db_session.query(Producer).filter_by(name="CTU/MINSA").one()

    result = compute_entrada_tariff(db_session, producer, weight=10.0)

    assert result.tariff_amount == 1200.0
    assert result.exceptions == []


def test_compute_entrada_tariff_per_weight_mode_missing_weight_flags_exception(db_session):
    producer = db_session.query(Producer).filter_by(name="CTU/MINSA").one()

    result = compute_entrada_tariff(db_session, producer, weight=None)

    assert result.tariff_amount is None
    assert "missing_expected_weight" in result.exceptions
    # Not a silent flat-rate fallback -- the matched rule is still surfaced
    # for traceability, but no amount is guessed.
    assert result.matched_rule is not None


def test_compute_entrada_tariff_none_producer_flags_exception(db_session):
    result = compute_entrada_tariff(db_session, None, weight=100.0)

    assert result.tariff_amount is None
    assert "unknown_tariff" in result.exceptions


def test_compute_entrada_tariff_producer_with_no_pricing_rule_flags_exception(db_session):
    producer = Producer(name="TEST No Pricing Producer", active=True)
    db_session.add(producer)
    db_session.flush()

    result = compute_entrada_tariff(db_session, producer, weight=100.0)

    assert result.tariff_amount is None
    assert "unknown_tariff" in result.exceptions


# --- compute_salida_tariff (Phase 3) ---------------------------------------


def test_compute_salida_tariff_per_weight_multiplies_rate_by_delivered_weight(db_session):
    # Real placeholder data: PricingRule P004 (Mina San Jose, per_weight, 0.10/kg).
    result = compute_salida_tariff(db_session, "Mina San Jose", delivered_weight=9000.0)

    assert result.tariff_amount == 900.0
    assert result.matched_rule is not None
    assert result.matched_rule.pricing_mode == "per_weight"
    assert result.exceptions == []


def test_compute_salida_tariff_missing_delivered_weight_flags_exception(db_session):
    # CFE always weighs -- a Salida PricingRule is always per_weight, so a
    # missing delivered_weight is never a silent flat-rate fallback.
    result = compute_salida_tariff(db_session, "Mina San Jose", delivered_weight=None)

    assert result.tariff_amount is None
    assert "missing_expected_weight" in result.exceptions
    assert result.matched_rule is not None


def test_compute_salida_tariff_unknown_origin_flags_exception(db_session):
    result = compute_salida_tariff(db_session, "Lugar Sin Tarifa", delivered_weight=1000.0)

    assert result.tariff_amount is None
    assert "unknown_tariff" in result.exceptions


def test_compute_salida_tariff_none_origin_flags_exception(db_session):
    result = compute_salida_tariff(db_session, None, delivered_weight=1000.0)

    assert result.tariff_amount is None
    assert "unknown_tariff" in result.exceptions
