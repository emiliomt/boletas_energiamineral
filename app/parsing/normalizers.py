"""Small normalization helpers shared by the field parser and classifier."""
from __future__ import annotations

import datetime as dt
import re

from rapidfuzz import fuzz, process

_DATE_PATTERNS = [
    # dd/mm/yy or dd-mm-yy (two-digit year)
    (re.compile(r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{2})\b"), "%d/%m/%y"),
    # dd/mm/yyyy or dd-mm-yyyy (four-digit year)
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


def _coerce_year(year_text: str) -> int:
    """Maps 2-digit years per business rule 00–69 → 2000–2069, 70–99 → 1970–1999.
    Leaves 4-digit years unchanged."""
    if len(year_text) == 2:
        yy = int(year_text)
        return (2000 + yy) if yy <= 69 else (1900 + yy)
    return int(year_text)


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
            return dt.date(_coerce_year(year), int(month), int(day)).isoformat()
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


_PAREN_RE = re.compile(r"\([^)]*\)")
_PHONE_DIGITS_RE = re.compile(r"\b\d{7,}\b")  # bare 7+ digit runs (phone-like)


def normalize_alias_text(value: str) -> str:
    """Strips parenthetical notes (e.g. phone numbers in parens) and bare
    phone-number-like digit runs from a raw transportista alias string,
    then collapses whitespace via clean_text. Used both when loading
    transportista_roster.csv and when resolving a raw name against the
    alias registry, so both sides compare on the same normalized form."""
    value = _PAREN_RE.sub(" ", value)
    value = _PHONE_DIGITS_RE.sub(" ", value)
    return clean_text(value)


_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")
_DIGIT_RUN_RE = re.compile(r"\d{3,}")

def normalize_truck_box_number(value: str | None) -> str | None:
    """Normalizes a 'No. Caja' value for matching:
    - Returns None for falsy input
    - Trims and lowercases
    - Removes spaces, hyphens and punctuation
    - If a 3+ digit run exists anywhere, returns the LAST such run (the significant
      core token), so variants like 'ROC 1274' and 'REC-1274' both normalize to '1274'
    - Otherwise returns the collapsed alphanumeric string (e.g. 'roc1274')
    """
    if not value:
        return None
    collapsed = _NON_ALNUM_RE.sub("", value).lower()
    digit_runs = list(_DIGIT_RUN_RE.finditer(collapsed))
    if digit_runs:
        return digit_runs[-1].group(0)
    return collapsed or None

_FOLIO_STRIP_RE = re.compile(r"[\s\-]+")

def normalize_folio(value: str | None) -> str | None:
    """Normalizes a folio for pairing/compare:
    - None → None
    - trim, lowercase
    - drop spaces and hyphens
    Example: 'B-1001' / 'B 1001' → 'b1001'
    """
    if not value:
        return None
    v = value.strip().lower()
    v = _FOLIO_STRIP_RE.sub("", v)
    return v or None
