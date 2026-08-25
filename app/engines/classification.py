"""Trip classification: matches parsed origin/destination (+material) against
the RouteRule config table to determine trip_type, inventory_direction, and
distance_band. Purely rule-based — exact match first, fuzzy match as a
fallback for OCR/typing noise, nothing found otherwise."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import Producer, RouteRule
from app.parsing.normalizers import best_fuzzy_match
from app.rules.config_loader import get_active_routes

FUZZY_SCORE_CUTOFF = 80.0  # 0-100 rapidfuzz scale


@dataclass
class ClassificationResult:
    trip_type: str | None = None
    confidence: float = 0.0  # 1.0 exact match, 0.5-0.8 fuzzy, 0.0 unmatched
    matched_rule: RouteRule | None = None
    matched_rule_note: str | None = None  # e.g. "exact" or "fuzzy:0.86"
    exceptions: list[str] = field(default_factory=list)


def classify_trip(db: Session, origin: str | None, destination: str | None) -> ClassificationResult:
    if not origin or not destination:
        return ClassificationResult(exceptions=["unknown_route"])

    routes = get_active_routes(db)
    if not routes:
        return ClassificationResult(exceptions=["unknown_route"])

    origin_norm, dest_norm = origin.strip().lower(), destination.strip().lower()

    # 1) Exact match (case-insensitive) on both origin and destination.
    for rule in routes:
        if rule.origin.strip().lower() == origin_norm and rule.destination.strip().lower() == dest_norm:
            return ClassificationResult(
                trip_type=rule.trip_type, confidence=1.0, matched_rule=rule, matched_rule_note="exact"
            )

    # 2) Fuzzy match: find the route whose (origin, destination) pair best
    #    matches, requiring both sides to individually clear the cutoff.
    best: tuple[RouteRule, float] | None = None
    for rule in routes:
        origin_match = best_fuzzy_match(origin, [rule.origin], score_cutoff=FUZZY_SCORE_CUTOFF)
        dest_match = best_fuzzy_match(destination, [rule.destination], score_cutoff=FUZZY_SCORE_CUTOFF)
        if origin_match and dest_match:
            combined = (origin_match[1] + dest_match[1]) / 2
            if best is None or combined > best[1]:
                best = (rule, combined)

    if best is not None:
        rule, score = best
        return ClassificationResult(
            trip_type=rule.trip_type,
            confidence=round(score, 2),
            matched_rule=rule,
            matched_rule_note=f"fuzzy:{score:.2f}",
        )

    return ClassificationResult(exceptions=["unknown_route"])


def classify_entrada(db: Session, producer: Producer | None) -> ClassificationResult:
    """Entrada classification (Phase 2): the boleta itself never carries an
    origin/destination pair to OCR -- the format is the producer's, not
    ours, and the destination is always implicitly "our patio". So instead
    of matching parsed text, this matches the selected Producer's
    `default_origin` against RouteRule.origin only, ignoring destination
    entirely. trip_type/inventory_direction still come from the matched
    RouteRule (e.g. the `recepcion_compra`/inbound placeholder route), so
    tariff/inventory computation downstream needs no Entrada-specific
    changes there.
    """
    if producer is None or not producer.active or not producer.default_origin:
        return ClassificationResult(exceptions=["unknown_producer"])

    routes = get_active_routes(db)
    if not routes:
        return ClassificationResult(exceptions=["unknown_route"])

    origin_norm = producer.default_origin.strip().lower()

    # 1) Exact match (case-insensitive) on origin only.
    for rule in routes:
        if rule.origin.strip().lower() == origin_norm:
            return ClassificationResult(
                trip_type=rule.trip_type,
                confidence=1.0,
                matched_rule=rule,
                matched_rule_note="exact:producer_default_origin",
            )

    # 2) Fuzzy match on origin only.
    best_entrada: tuple[RouteRule, float] | None = None
    for rule in routes:
        origin_match = best_fuzzy_match(producer.default_origin, [rule.origin], score_cutoff=FUZZY_SCORE_CUTOFF)
        if origin_match and (best_entrada is None or origin_match[1] > best_entrada[1]):
            best_entrada = (rule, origin_match[1])

    if best_entrada is not None:
        rule, score = best_entrada
        return ClassificationResult(
            trip_type=rule.trip_type,
            confidence=round(score, 2),
            matched_rule=rule,
            matched_rule_note=f"fuzzy:{score:.2f}",
        )

    return ClassificationResult(exceptions=["unknown_route"])
