"""Date normalization, including spelled-out month names that hand-filled
boletas commonly use (e.g. "19/Agosto/2026")."""
from __future__ import annotations

import pytest

from app.parsing.normalizers import parse_date


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Fecha: 20/01/2026", "2026-01-20"),
        ("Fecha: 2026-01-20", "2026-01-20"),
        ("Fecha: 19/Agosto/2026", "2026-08-19"),
        ("Fecha: 19 de agosto de 2026", "2026-08-19"),
        ("Fecha: 19 Agosto 2026", "2026-08-19"),
        ("Fecha: 5-Ene-2026", "2026-01-05"),
        ("Fecha: 3 de diciembre de 2025", "2025-12-03"),
    ],
)
def test_parse_date_handles_numeric_and_textual_months(text, expected):
    assert parse_date(text) == expected


def test_parse_date_returns_none_when_absent():
    assert parse_date("no date here") is None


def test_parse_date_ignores_unknown_month_word():
    assert parse_date("Fecha: 19/Blahmonth/2026") is None
