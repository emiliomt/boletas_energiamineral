"""Tariff lookup: trip_type (+ distance_band from the matched route) -> the
amount owed to the fletero. Purely a config-table lookup — flags an
exception if no rule covers the classified trip."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import PricingRule, Producer, TariffRule
from app.rules.config_loader import get_active_pricing_rules, get_active_tariffs


@dataclass
class TariffResult:
    tariff_amount: float | None = None
    matched_rule: TariffRule | None = None
    exceptions: list[str] = field(default_factory=list)


def compute_tariff(db: Session, trip_type: str | None, distance_band: str | None) -> TariffResult:
    if not trip_type:
        return TariffResult(exceptions=["unknown_tariff"])

    tariffs = get_active_tariffs(db)
    candidates = [t for t in tariffs if t.trip_type == trip_type]
    if not candidates:
        return TariffResult(exceptions=["unknown_tariff"])

    # Prefer an exact distance_band match; fall back to a rule banded "any".
    if distance_band:
        for t in candidates:
            if t.distance_band == distance_band:
                return TariffResult(tariff_amount=t.tariff_amount, matched_rule=t)

    for t in candidates:
        if t.distance_band == "any":
            return TariffResult(tariff_amount=t.tariff_amount, matched_rule=t)

    return TariffResult(exceptions=["unknown_tariff"])


@dataclass
class PricingRuleTariffResult:
    """Shared result shape for both PricingRule-driven flows: Entrada
    (Phase 2, scope=Producer.name) and Salida (Phase 3, scope=origin)."""

    tariff_amount: float | None = None
    matched_rule: PricingRule | None = None
    exceptions: list[str] = field(default_factory=list)


def _compute_pricing_rule_tariff(db: Session, scope: str | None, weight: float | None) -> PricingRuleTariffResult:
    """Looks up the active PricingRule whose `scope` matches (case-
    insensitive), then branches on `pricing_mode`:

    - "flat": tariff_amount = rate, regardless of weight.
    - "per_weight" with a weight: tariff_amount = rate * weight.
    - "per_weight" with no weight: an exception, not a silent flat-rate
      fallback -- flagged `missing_expected_weight` and routed to review
      instead of guessing a price.
    """
    if not scope:
        return PricingRuleTariffResult(exceptions=["unknown_tariff"])

    scope_norm = scope.strip().lower()
    candidates = [r for r in get_active_pricing_rules(db) if r.scope.strip().lower() == scope_norm]
    if not candidates:
        return PricingRuleTariffResult(exceptions=["unknown_tariff"])

    rule = candidates[0]
    if rule.pricing_mode == "per_weight":
        if weight is None:
            return PricingRuleTariffResult(matched_rule=rule, exceptions=["missing_expected_weight"])
        return PricingRuleTariffResult(tariff_amount=rule.rate * weight, matched_rule=rule)

    # flat
    return PricingRuleTariffResult(tariff_amount=rule.rate, matched_rule=rule)


def compute_entrada_tariff(db: Session, producer: Producer | None, weight: float | None) -> PricingRuleTariffResult:
    """Entrada pricing (Phase 2): if the producer has a rate-card
    `precio_transporte` (set in the Proveedores admin page), that flat MXN
    amount is the fletero tariff. Otherwise look up a PricingRule scoped to
    Producer.name (pricing_config.csv), as of today. See
    _compute_pricing_rule_tariff for the flat/per_weight branching."""
    if producer is None:
        return PricingRuleTariffResult(exceptions=["unknown_tariff"])
    if producer.precio_transporte is not None:
        return PricingRuleTariffResult(tariff_amount=producer.precio_transporte)
    return _compute_pricing_rule_tariff(db, producer.name, weight)


def compute_salida_tariff(db: Session, origin: str | None, delivered_weight: float | None) -> PricingRuleTariffResult:
    """Salida pricing (Phase 3): always per_weight -- CFE always weighs, so
    there's no flat-rate Salida case in practice, though a misconfigured
    flat PricingRule for a Salida-style scope is still handled the same as
    Entrada's (rate regardless of weight) rather than treated as an error,
    consistent with the config-is-the-source-of-truth philosophy. PricingRule
    scoped to the classified route's origin (Phase 1's Salida-style scope
    convention), looked up only once `salida_status == "complete"` --
    `delivered_weight` isn't known before both documents have reconciled."""
    return _compute_pricing_rule_tariff(db, origin, delivered_weight)
