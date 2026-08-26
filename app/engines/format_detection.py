"""Optional per-producer format auto-detection (Phase 4 PRD §6.2): searches
OCR text for each active BoletaFormatTemplate's `detection_signal`. Exactly
one match pre-selects that template; zero or multiple matches defer to
manual producer selection (Phase 2's existing upload flow) rather than
guessing -- conservative by design, consistent with the system's overall
philosophy of routing uncertainty to review rather than resolving it
silently.

Standalone and NOT wired into app/pipeline/orchestrator.py or the upload
UI: producer selection happens once per lote at Batch-creation time (Phase
2), a level above any single scan's OCR text, so auto-selecting a producer
per-scan doesn't fit the current upload flow without a larger UI change to
move producer selection to per-file. Proven here as a testable capability
instead -- the same precedent as Phase 1's standalone
resolve_transportista() -- consistent with PRD Phase 4 §10 Open Questions,
which explicitly allows shipping per-producer templates without detection
wired up in v1 and deferring that wiring decision to a later increment.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import BoletaFormatTemplate
from app.rules.config_loader import get_active_boleta_format_templates


@dataclass
class FormatDetectionResult:
    template: BoletaFormatTemplate | None = None
    # format_id(s) whose detection_signal matched -- populated even on a
    # no-match/ambiguous-match result, for diagnostics/logging.
    matched_format_ids: list[str] = field(default_factory=list)


def detect_format(db: Session, ocr_text: str | None) -> FormatDetectionResult:
    """Returns the single unambiguously-detected template, or an empty
    result (with the ambiguous/no candidates recorded in
    `matched_format_ids` for diagnostics) when detection can't confidently
    pick one -- callers should fall back to manual selection in that case,
    never guess among multiple matches."""
    if not ocr_text:
        return FormatDetectionResult()

    text_lower = ocr_text.lower()
    templates = get_active_boleta_format_templates(db)
    matches = [
        t for t in templates
        if t.detection_signal and t.detection_signal.strip().lower() in text_lower
    ]
    if len(matches) == 1:
        return FormatDetectionResult(template=matches[0], matched_format_ids=[matches[0].format_id])
    return FormatDetectionResult(matched_format_ids=[t.format_id for t in matches])
