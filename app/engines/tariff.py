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
class EntradaTariffResult:
    tariff_amount: float | None = None
    matched_rule: PricingRule | None = None
    exceptions: list[str] = field(default_factory=list)


def compute_entrada_tariff(db: Session, producer: Producer | None, weight: float | None) -> EntradaTariffResult:
    """Entrada pricing (Phase 2): looks up the active `PricingRule` scoped
    to the selected producer (PricingRule.scope matches Producer.name --
    see Phase 1's pricing_config.csv convention) as of today, then branches
    on `pricing_mode`:

    - "flat": tariff_amount = rate, regardless of weight (weight is still
      used for inventory via WeightEstimationRule, just not for pricing).
    - "per_weight" with a parsed weight: tariff_amount = rate * weight.
    - "per_weight" with no weight: this is an exception, not a silent
      flat-rate fallback -- the boleta's format is supposed to carry a
      weight and didn't, so it's flagged `missing_expected_weight` and
      routed to review instead of guessing a price.
    """
    if producer is None:
        return EntradaTariffResult(exceptions=["unknown_tariff"])

    scope_norm = producer.name.strip().lower()
    candidates = [r for r in get_active_pricing_rules(db) if r.scope.strip().lower() == scope_norm]
    if not candidates:
        return EntradaTariffResult(exceptions=["unknown_tariff"])

    rule = candidates[0]
    if rule.pricing_mode == "per_weight":
        if weight is None:
            return EntradaTariffResult(matched_rule=rule, exceptions=["missing_expected_weight"])
        return EntradaTariffResult(tariff_amount=rule.rate * weight, matched_rule=rule)

    # flat
    return EntradaTariffResult(tariff_amount=rule.rate, matched_rule=rule)
