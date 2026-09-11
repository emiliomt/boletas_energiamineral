"""Parse WhatsApp operator commands (no HTTP / Twilio)."""
from __future__ import annotations

import pytest

from app.whatsapp.commands import parse_command
from app.whatsapp.numbers import normalize_sender, sender_is_allowed


@pytest.mark.parametrize(
    ("body", "kind", "arg"),
    [
        ("ayuda", "help", None),
        ("HELP", "help", None),
        ("hola", "help", None),
        ("estado", "status", None),
        ("fin", "close", None),
        ("cerrar", "close", None),
        ("cfe", "doc_type", "cfe_slip"),
        ("boleta", "doc_type", "boleta"),
        ("tipo entrada", "set_kind", "entrada"),
        ("Entrada", "set_kind", "entrada"),
        ("tipo salida", "set_kind", "salida"),
        ("lote nuevo", "new_lote", None),
        ("lote nuevo Turno noche", "new_lote", "Turno noche"),
        ("lote 12", "bind_lote", "12"),
        ("lote #12", "bind_lote", "12"),
        ("lote Camión Norte", "bind_lote", "Camión Norte"),
        ("productor Bradfort", "set_producer", "Bradfort"),
    ],
)
def test_parse_command(body, kind, arg):
    command = parse_command(body)
    assert command is not None
    assert command.kind == kind
    assert command.arg == arg


def test_photo_captions_are_not_commands():
    assert parse_command("boleta de Juan") is None
    assert parse_command("foto nítida") is None
    assert parse_command("") is None


def test_normalize_sender_strips_whatsapp_prefix():
    assert normalize_sender("whatsapp:+5491112345678") == "+5491112345678"
    assert normalize_sender("+5491112345678") == "+5491112345678"


def test_allowlist_matches_last_ten_digits(monkeypatch):
    import app.config as app_config

    monkeypatch.setattr(app_config.settings, "whatsapp_allowed_senders", "+5491112345678")
    assert sender_is_allowed("+5491112345678")
    assert sender_is_allowed("1112345678")  # last 10 digits
    assert not sender_is_allowed("+5491100000000")


def test_empty_allowlist_accepts_anyone(monkeypatch):
    import app.config as app_config

    monkeypatch.setattr(app_config.settings, "whatsapp_allowed_senders", "")
    assert sender_is_allowed("+15551212")
