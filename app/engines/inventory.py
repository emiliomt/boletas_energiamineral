"""Inventory movement: turns the classified route's inventory_direction plus
a measured or estimated weight into a signed inventory quantity. Trips whose
route has inventory_direction "none" (e.g. an internal transfer) don't move
net inventory, so a missing weight there is not an exception."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import RouteRule, WeightEstimationRule
from app.rules.config_loader import get_active_weight_rules


@dataclass
class InventoryResult:
    inventory_direction: str = "unknown"
    inventory_quantity: float | None = None
    weight: float | None = None
    weight_source: str = "missing"  # measured | estimated | missing
    matched_weight_rule: WeightEstimationRule | None = None
    exceptions: list[str] = field(default_factory=list)


def compute_inventory(
    db: Session,
    route_rule: RouteRule | None,
    material: str | None,
    measured_weight: float | None,
) -> InventoryResult:
    direction = route_rule.inventory_direction if route_rule else "unknown"
    result = InventoryResult(inventory_direction=direction)
    exceptions: list[str] = []

    if direction == "unknown":
        exceptions.append("unknown_inventory_direction")

    if measured_weight is not None:
        result.weight = measured_weight
        result.weight_source = "measured"
    elif route_rule is not None:
        # Weight missing on the scan — look for an applicable estimation rule.
        for rule in get_active_weight_rules(db):
            if rule.trip_type != route_rule.trip_type:
                continue
            if rule.material and material and rule.material.strip().lower() != material.strip().lower():
                continue
            result.weight = rule.estimated_weight_kg
            result.weight_source = "estimated"
            result.matched_weight_rule = rule
            break

    if result.weight is None:
        result.weight_source = "missing"
        if direction in ("inbound", "outbound"):
            exceptions.append("missing_weight_no_estimate")

    if direction == "inbound":
        result.inventory_quantity = result.weight
    elif direction == "outbound":
        result.inventory_quantity = -result.weight if result.weight is not None else None
    else:
        result.inventory_quantity = None  # "none" or "unknown": no net inventory effect to post

    result.exceptions = exceptions
    return result
