"""Resolves a raw (OCR'd or hand-typed) transportista/driver name string
against the canonical Transportista roster + aliases. Same shape as the
other lookup engines (classification.py/tariff.py/folio_registry.py): a
dataclass result carrying its own `.exceptions` list.

Wired into app/pipeline/orchestrator.py starting Phase 2, for Entrada
boletas only (per that PRD's goals) -- Salida records don't run this yet,
so the `unmatched_transportista` exception can't regress the existing
Salida flow.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import Transportista
from app.parsing.normalizers import best_fuzzy_match, normalize_alias_text
from app.rules.config_loader import get_active_transportistas

FUZZY_SCORE_CUTOFF = 80.0


@dataclass
class TransportistaResolution:
    transportista: Transportista | None = None
    matched_alias: str | None = None  # the canonical name or normalized alias_text that matched
    confidence: float = 0.0  # 1.0 exact, 0.80-0.99 fuzzy, 0.0 unmatched
    match_note: str | None = None  # "exact:canonical" | "exact:alias" | "fuzzy:0.86"
    exceptions: list[str] = field(default_factory=list)  # "unmatched_transportista"; inert until Phase 2/3 wiring


def resolve_transportista(db: Session, raw_name: str | None) -> TransportistaResolution:
    if not raw_name:
        return TransportistaResolution(exceptions=["unmatched_transportista"])

    normalized = normalize_alias_text(raw_name)
    transportistas = get_active_transportistas(db)
    if not transportistas:
        return TransportistaResolution(exceptions=["unmatched_transportista"])

    choice_map: dict[str, Transportista] = {}
    for t in transportistas:
        choice_map[t.canonical_name] = t
        for alias in t.aliases:
            if alias.active:
                choice_map[alias.alias_text] = t

    normalized_lower = normalized.strip().lower()
    for choice, t in choice_map.items():
        if choice.strip().lower() == normalized_lower:
            is_canonical = choice == t.canonical_name
            return TransportistaResolution(
                transportista=t,
                matched_alias=choice,
                confidence=1.0,
                match_note="exact:canonical" if is_canonical else "exact:alias",
            )

    match = best_fuzzy_match(normalized, list(choice_map.keys()), score_cutoff=FUZZY_SCORE_CUTOFF)
    if match is not None:
        choice, score = match
        t = choice_map[choice]
        return TransportistaResolution(
            transportista=t,
            matched_alias=choice,
            confidence=round(score, 2),
            match_note=f"fuzzy:{score:.2f}",
        )

    return TransportistaResolution(exceptions=["unmatched_transportista"])
