"""Unit tests for the standalone format auto-detection capability (Phase 4
PRD §6.2/§8): unambiguous match, no match, multiple matches -- all three
must defer to manual selection except the unambiguous case."""
from __future__ import annotations

from app.engines.format_detection import detect_format
from app.models import BoletaFormatTemplate, Producer


def _seed_template(db_session, producer_name: str, format_id: str, detection_signal: str | None) -> BoletaFormatTemplate:
    producer = db_session.query(Producer).filter_by(name=producer_name).one()
    template = BoletaFormatTemplate(
        format_id=format_id,
        producer_id=producer.id,
        label_patterns_json="{}",
        expects_weight=True,
        detection_signal=detection_signal,
        active=True,
    )
    db_session.add(template)
    db_session.flush()
    return template


def test_unambiguous_signal_selects_the_one_template(db_session):
    _seed_template(db_session, "CTU/MINSA", "TEST-CTU", detection_signal="CTU UNIQUE MARK")
    _seed_template(db_session, "Bradfort", "TEST-BRAD", detection_signal="BRADFORT UNIQUE MARK")

    result = detect_format(db_session, "Some header text\nCTU UNIQUE MARK\nFolio: E-1\n")

    assert result.template is not None
    assert result.template.format_id == "TEST-CTU"
    assert result.matched_format_ids == ["TEST-CTU"]


def test_no_matching_signal_defers_to_manual_selection(db_session):
    _seed_template(db_session, "CTU/MINSA", "TEST-CTU", detection_signal="CTU UNIQUE MARK")

    result = detect_format(db_session, "Totally unrelated scan text with no known marks.")

    assert result.template is None
    assert result.matched_format_ids == []


def test_multiple_matching_signals_defers_to_manual_selection(db_session):
    # Both signals happen to appear in one deliberately ambiguous scan --
    # detection must not guess between them.
    _seed_template(db_session, "CTU/MINSA", "TEST-CTU", detection_signal="MARCA")
    _seed_template(db_session, "Bradfort", "TEST-BRAD", detection_signal="MARCA")

    result = detect_format(db_session, "Encabezado con MARCA en el texto.")

    assert result.template is None
    assert sorted(result.matched_format_ids) == ["TEST-BRAD", "TEST-CTU"]


def test_no_ocr_text_defers_to_manual_selection(db_session):
    _seed_template(db_session, "CTU/MINSA", "TEST-CTU", detection_signal="CTU UNIQUE MARK")

    result = detect_format(db_session, "")

    assert result.template is None
    assert result.matched_format_ids == []


def test_inactive_template_is_never_a_detection_candidate(db_session):
    template = _seed_template(db_session, "CTU/MINSA", "TEST-CTU", detection_signal="CTU UNIQUE MARK")
    template.active = False
    db_session.flush()

    result = detect_format(db_session, "Scan containing CTU UNIQUE MARK somewhere.")

    assert result.template is None
    assert result.matched_format_ids == []
