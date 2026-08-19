"""Small normalization helpers shared by the field parser and classifier."""
from __future__ import annotations

import datetime as dt
import re

from rapidfuzz import fuzz, process

_DATE_PATTERNS = [
    # dd/mm/yyyy or dd-mm-yyyy
    (re.compile(r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})\b"), "%d/%m/%Y"),
    # yyyy-mm-dd
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), "%Y-%m-%d"),
]

# Spanish (and a few English) month names -> month number. Boletas are often
# hand-dated with a spelled-out month (e.g. "19/Agosto/2026" or "19 de agosto
# de 2026"), which the numeric-only patterns above can't read. Accents are
# optional since OCR drops them.
_MONTHS = {
    "enero": 1, "ene": 1, "january": 1, "jan": 1,
    "febrero": 2, "feb": 2, "february": 2,
    "marzo": 3, "mar": 3, "march": 3,
    "abril": 4, "abr": 4, "april": 4, "apr": 4,
    "mayo": 5, "may": 5,
    "junio": 6, "jun": 6, "june": 6,
    "julio": 7, "jul": 7, "july": 7,
    "agosto": 8, "ago": 8, "agost": 8, "august": 8, "aug": 8,
    "septiembre": 9, "setiembre": 9, "sep": 9, "sept": 9, "september": 9,
    "octubre": 10, "oct": 10, "october": 10,
    "noviembre": 11, "nov": 11, "november": 11,
    "diciembre": 12, "dic": 12, "december": 12, "dec": 12,
}
# e.g. "19 de agosto de 2026", "19/Agosto/2026", "19-ago-2026", "19 agosto 2026".
# Separator allows spaces, slashes, dashes and an optional "de". The year is
# the last 4 digits of its run (\d*(\d{4})) so an OCR stray digit like
# "19/Agosto 12026" still yields 2026.
_TEXTUAL_SEP = r"[\s/\-]*(?:de\s+)?[\s/\-]*"
_TEXTUAL_DATE_PATTERN = re.compile(
    r"\b(\d{1,2})" + _TEXTUAL_SEP + r"([A-Za-zÁÉÍÓÚáéíóúñ]+)\.?" + _TEXTUAL_SEP + r"\d*(\d{4})\b",
    re.IGNORECASE,
)


def parse_date(text: str) -> str | None:
    """Extracts the first recognizable date in `text` and returns ISO YYYY-MM-DD, or None.

    Handles numeric dates (dd/mm/yyyy, yyyy-mm-dd) and spelled-out month names
    common on hand-filled boletas (e.g. "19/Agosto/2026")."""
    for pattern, _fmt in _DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        groups = match.groups()
        try:
            if len(groups[0]) == 4:  # yyyy-mm-dd
                year, month, day = groups
            else:  # dd/mm/yyyy
                day, month, year = groups
            return dt.date(int(year), int(month), int(day)).isoformat()
        except ValueError:
            continue

    for match in _TEXTUAL_DATE_PATTERN.finditer(text):
        day, month_name, year = match.groups()
        month = _MONTHS.get(month_name.lower().strip("."))
        if not month:
            continue  # not a real month word (e.g. "1 informe 2026") -- keep scanning
        try:
            return dt.date(int(year), month, int(day)).isoformat()
        except ValueError:
            continue
    return None


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t\r\n:.-")


def parse_weight_kg(text: str) -> float | None:
    """Finds a number near a weight unit (kg/ton/tonelada) and returns kilograms."""
    match = re.search(r"(\d+[.,]?\d*)\s*(kg|kilos?|ton(?:elada)?s?)", text, re.IGNORECASE)
    if not match:
        return None
    raw_value = float(match.group(1).replace(",", "."))
    unit = match.group(2).lower()
    if unit.startswith("ton"):
        return raw_value * 1000
    return raw_value


def best_fuzzy_match(value: str, choices: list[str], score_cutoff: float = 80.0) -> tuple[str, float] | None:
    """Returns (best_choice, score 0-1) if the fuzzy match clears score_cutoff, else None."""
    if not value or not choices:
        return None
    result = process.extractOne(value, choices, scorer=fuzz.token_sort_ratio, score_cutoff=score_cutoff)
    if result is None:
        return None
    choice, score, _idx = result
    return choice, score / 100.0
