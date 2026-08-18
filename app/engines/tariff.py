"""Tariff lookup: trip_type (+ distance_band from the matched route) -> the
amount owed to the fletero. Purely a config-table lookup — flags an
exception if no rule covers the classified trip."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import TariffRule
from app.rules.config_loader import get_active_tariffs


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
