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


def parse_date(text: str) -> str | None:
    """Extracts the first recognizable date in `text` and returns ISO YYYY-MM-DD, or None."""
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
