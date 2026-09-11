"""Normalize WhatsApp sender IDs and the optional allowlist."""
from __future__ import annotations

from app.config import settings


def normalize_sender(from_param: str) -> str:
    """`whatsapp:+5215551234567` -> `+5215551234567`."""
    raw = (from_param or "").strip()
    if raw.lower().startswith("whatsapp:"):
        raw = raw.split(":", 1)[1]
    raw = raw.strip()
    if raw and not raw.startswith("+") and raw.lstrip().lstrip("+").isdigit():
        raw = "+" + raw.lstrip("+")
    return raw


def digits_only(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def sender_is_allowed(sender: str) -> bool:
    """Empty allowlist accepts every sender (sandbox / first-run). When set,
    a number matches if its digit string equals an entry or they share the
    same last 10 digits (MX mobile with/without country code)."""
    raw = (settings.whatsapp_allowed_senders or "").strip()
    if not raw:
        return True
    sender_digits = digits_only(sender)
    if not sender_digits:
        return False
    for entry in raw.split(","):
        allowed = digits_only(entry)
        if not allowed:
            continue
        if sender_digits == allowed:
            return True
        if sender_digits[-10:] == allowed[-10:] and len(allowed) >= 10:
            return True
    return False
