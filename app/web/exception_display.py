"""Turns raw exception codes (e.g. "unknown_route", "missing_required_field:date")
into reviewer-friendly, severity-ranked display metadata for the web UI.

Severity tiers (most to least important):
  - "alta"  (critical): blocks trust in the record itself -- duplicates,
            reused/unknown folios, volume mismatches, missing required fields.
  - "media" (warning): the boleta was read fine but a business rule couldn't be
            resolved yet (route/tariff/inventory not configured).
  - "baja"  (info): soft quality flags (low OCR/field confidence).
"""
from __future__ import annotations

_SEVERITY_ORDER = {"alta": 0, "media": 1, "baja": 2}
_SEVERITY_LABEL = {"alta": "Crítico", "media": "Advertencia", "baja": "Aviso"}

# Static code -> (severity, human label).
_EXCEPTION_META: dict[str, tuple[str, str]] = {
    "suspected_duplicate": ("alta", "Posible boleta duplicada"),
    "folio_already_used": ("alta", "Folio ya utilizado en otra boleta"),
    "unknown_folio": ("alta", "Folio no emitido / desconocido"),
    "volumen_mismatch": ("alta", "Diferencia entre volumen entregado y por entregar"),
    "missing_weight_no_estimate": ("alta", "Falta el peso y no hay estimación"),
    "unknown_route": ("media", "Ruta no reconocida (revisar configuración)"),
    "unknown_tariff": ("media", "Tarifa no encontrada para el tipo de viaje"),
    "unknown_inventory_direction": ("media", "Dirección de inventario desconocida"),
    "low_ocr_confidence": ("baja", "Baja confianza general del OCR"),
}

# Spanish labels for field names used in `missing_required_field:<field>` and
# `low_field_confidence:<field>` codes.
_FIELD_LABELS = {
    "folio": "Folio",
    "date": "Fecha",
    "origin": "Origen (Centro de Explotación)",
    "destination": "Destino",
    "fletero": "Fletero",
}


def describe_exception(code: str) -> dict:
    """Maps one exception code to {code, severity, severity_label, label}."""
    severity, label = _EXCEPTION_META.get(code, ("media", code))

    if ":" in code:
        prefix, _, arg = code.partition(":")
        field_label = _FIELD_LABELS.get(arg, arg)
        if prefix == "missing_required_field":
            severity, label = "alta", f"Falta campo obligatorio: {field_label}"
        elif prefix == "low_field_confidence":
            severity, label = "baja", f"Baja confianza en el campo: {field_label}"

    return {
        "code": code,
        "severity": severity,
        "severity_label": _SEVERITY_LABEL[severity],
        "label": label,
    }


def describe_exceptions(codes: list[str] | None) -> list[dict]:
    """Describes and sorts codes most-important-first (stable within a tier)."""
    described = [describe_exception(c) for c in (codes or [])]
    return sorted(described, key=lambda e: _SEVERITY_ORDER.get(e["severity"], 1))
