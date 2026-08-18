from __future__ import annotations

from app.engines.inventory import compute_inventory
from app.models import RouteRule


def _route(db_session, route_id: str) -> RouteRule:
    return db_session.query(RouteRule).filter_by(route_id=route_id).one()


def test_measured_weight_used_directly_outbound(db_session):
    route = _route(db_session, "R001")  # acarreo_carbon, outbound

    result = compute_inventory(db_session, route, "carbon", measured_weight=9000.0)

    assert result.weight == 9000.0
    assert result.weight_source == "measured"
    assert result.inventory_direction == "outbound"
    assert result.inventory_quantity == -9000.0
    assert result.exceptions == []


def test_inbound_route_gives_positive_quantity(db_session):
    route = _route(db_session, "R004")  # recepcion_compra, inbound

    result = compute_inventory(db_session, route, "carbon", measured_weight=5000.0)

    assert result.inventory_direction == "inbound"
    assert result.inventory_quantity == 5000.0


def test_missing_weight_uses_estimation_rule(db_session):
    route = _route(db_session, "R002")  # transferencia_interna, none

    result = compute_inventory(db_session, route, "carbon", measured_weight=None)

    assert result.weight == 8000.0
    assert result.weight_source == "estimated"
    assert result.matched_weight_rule is not None
    assert result.exceptions == []  # direction "none" -> no inventory-quantity impact either way


def test_missing_weight_no_estimate_rule_flags_exception(db_session):
    route = _route(db_session, "R001")  # acarreo_carbon, outbound, no matching weight rule

    result = compute_inventory(db_session, route, "carbon", measured_weight=None)

    assert result.weight is None
    assert result.weight_source == "missing"
    assert "missing_weight_no_estimate" in result.exceptions


def test_unknown_route_flags_unknown_inventory_direction(db_session):
    result = compute_inventory(db_session, None, "carbon", measured_weight=1000.0)

    assert result.inventory_direction == "unknown"
    assert "unknown_inventory_direction" in result.exceptions
