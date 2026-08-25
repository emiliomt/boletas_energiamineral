"""Exception codes are mapped to reviewer-friendly, severity-ranked display data."""
from __future__ import annotations

from app.web.exception_display import describe_exception, describe_exceptions, summarize_exceptions


def test_static_codes_map_to_severity_and_label():
    dup = describe_exception("suspected_duplicate")
    assert dup["severity"] == "alta"
    assert dup["severity_label"] == "Crítico"
    assert "duplicada" in dup["label"].lower()

    route = describe_exception("unknown_route")
    assert route["severity"] == "media"
    assert route["severity_label"] == "Advertencia"

    ocr = describe_exception("low_ocr_confidence")
    assert ocr["severity"] == "baja"
    assert ocr["severity_label"] == "Aviso"


def test_dynamic_field_codes_are_expanded():
    missing = describe_exception("missing_required_field:date")
    assert missing["severity"] == "alta"
    assert "Fecha" in missing["label"]

    low = describe_exception("low_field_confidence:fletero")
    assert low["severity"] == "baja"
    assert "Fletero" in low["label"]


def test_unknown_code_degrades_gracefully():
    d = describe_exception("some_new_code")
    assert d["code"] == "some_new_code"
    assert d["label"] == "some_new_code"  # falls back to the raw code
    assert d["severity"] in {"alta", "media", "baja"}


def test_exceptions_sorted_most_important_first():
    codes = ["low_ocr_confidence", "unknown_route", "suspected_duplicate", "missing_required_field:folio"]
    severities = [e["severity"] for e in describe_exceptions(codes)]
    # alta entries first, then media, then baja
    assert severities == ["alta", "alta", "media", "baja"]


def test_summarize_exceptions_rolls_up_counts_by_severity():
    codes = [
        "suspected_duplicate", "folio_already_used",  # 2 alta
        "unknown_route", "unknown_tariff", "unknown_inventory_direction",  # 3 media
        "low_ocr_confidence",  # 1 baja
    ]
    summary = summarize_exceptions(codes)
    assert [(g["severity"], g["count"]) for g in summary] == [("alta", 2), ("media", 3), ("baja", 1)]
    # full labels are retained (for a hover tooltip)
    assert len(summary[0]["labels"]) == 2


def test_summarize_exceptions_empty():
    assert summarize_exceptions([]) == []
    assert summarize_exceptions(None) == []
