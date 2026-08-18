from __future__ import annotations

from app.engines.tariff import compute_tariff


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
