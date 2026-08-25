from __future__ import annotations

from app.engines.transportista_registry import resolve_transportista
from app.models import Transportista, TransportistaAlias
from app.parsing.normalizers import normalize_alias_text


def _seed_transportista(db_session, canonical: str, aliases: list[str]) -> Transportista:
    t = Transportista(canonical_name=canonical)
    db_session.add(t)
    db_session.flush()
    for raw_alias in aliases:
        db_session.add(
            TransportistaAlias(
                transportista_id=t.id,
                alias_text=normalize_alias_text(raw_alias),
                raw_alias_text=raw_alias,
            )
        )
    db_session.flush()
    return t


def test_resolves_via_canonical_name_exact(db_session):
    _seed_transportista(db_session, "TEST CANONICAL DRIVER", [])

    result = resolve_transportista(db_session, "TEST CANONICAL DRIVER")

    assert result.transportista is not None
    assert result.transportista.canonical_name == "TEST CANONICAL DRIVER"
    assert result.confidence == 1.0
    assert result.match_note == "exact:canonical"
    assert result.exceptions == []


def test_resolves_via_alias_exact(db_session):
    _seed_transportista(db_session, "TEST CANONICAL DRIVER 2", ["TEST ALIAS X"])

    result = resolve_transportista(db_session, "TEST ALIAS X")

    assert result.transportista is not None
    assert result.transportista.canonical_name == "TEST CANONICAL DRIVER 2"
    assert result.confidence == 1.0
    assert result.match_note == "exact:alias"


def test_resolves_correctly_against_either_of_two_aliases(db_session):
    # Mirrors the PRD's own example ("ABREGO O TRANSPORTES MAU" is one
    # entity known by two names) with test-prefixed names so this doesn't
    # collide with the identical example already seeded from the real
    # placeholder transportista_roster.csv via the db_session fixture.
    _seed_transportista(
        db_session,
        "TEST ABREGO O TRANSPORTES MAU",
        ["TEST ABREGO O TRANSPORTES MAU", "TEST TRANSPORTES MAU"],
    )

    result_a = resolve_transportista(db_session, "TEST ABREGO O TRANSPORTES MAU")
    result_b = resolve_transportista(db_session, "TEST TRANSPORTES MAU")

    assert result_a.transportista is not None
    assert result_b.transportista is not None
    assert result_a.transportista.id == result_b.transportista.id
    assert result_a.transportista.canonical_name == "TEST ABREGO O TRANSPORTES MAU"


def test_resolves_via_fuzzy_match_on_typo(db_session):
    _seed_transportista(db_session, "TEST CAMAGO", ["TEST CAMAGO"])

    result = resolve_transportista(db_session, "TEST CAMAGOO")

    assert result.transportista is not None
    assert result.transportista.canonical_name == "TEST CAMAGO"
    assert 0.80 <= result.confidence < 1.0
    assert result.match_note.startswith("fuzzy:")


def test_alias_with_phone_number_resolves_when_queried_without_phone(db_session):
    _seed_transportista(db_session, "TEST POLO RODRIGUEZ", ["TEST POLO RODRIGUEZ (8641006935)"])

    result = resolve_transportista(db_session, "TEST POLO RODRIGUEZ")

    assert result.transportista is not None
    assert result.transportista.canonical_name == "TEST POLO RODRIGUEZ"
    assert result.confidence == 1.0


def test_no_match_returns_unmatched_transportista_exception(db_session):
    _seed_transportista(db_session, "TEST SOMEONE ELSE", [])

    result = resolve_transportista(db_session, "TEST COMPLETELY UNRELATED NAME ZZZ")

    assert result.transportista is None
    assert "unmatched_transportista" in result.exceptions


def test_empty_roster_returns_unmatched_transportista_exception(db_session):
    # No transportistas seeded beyond whatever placeholder CSV data loaded
    # (which won't match this made-up name), so this exercises the "no
    # match" path when there's effectively nothing relevant in the roster.
    result = resolve_transportista(db_session, "TEST NAME NOT IN ANY ROSTER 12345")

    assert result.transportista is None
    assert "unmatched_transportista" in result.exceptions


def test_none_raw_name_returns_unmatched_transportista_exception(db_session):
    result = resolve_transportista(db_session, None)

    assert result.transportista is None
    assert "unmatched_transportista" in result.exceptions
