from __future__ import annotations

import pytest

from app.models import PricingRule, Producer, Transportista, TransportistaAlias
from app.rules.config_loader import (
    get_active_pricing_rules,
    load_pricing_rules,
    load_producers,
    load_transportistas,
)


def _write_csv(tmp_path, name: str, content: str):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


# --- load_producers ----------------------------------------------------


def test_load_producers_creates_rows(db_session, tmp_path):
    path = _write_csv(
        tmp_path,
        "producers.csv",
        "name,format_id,default_origin,active\n"
        "Test Producer A,FMT-1,Mina Test,true\n",
    )

    count = load_producers(db_session, csv_path=path)

    assert count == 1
    row = db_session.query(Producer).filter_by(name="Test Producer A").one()
    assert row.format_id == "FMT-1"
    assert row.default_origin == "Mina Test"
    assert row.active is True


def test_load_producers_upsert_by_name_is_idempotent(db_session, tmp_path):
    path = _write_csv(
        tmp_path,
        "producers.csv",
        "name,format_id,default_origin,active\nTest Producer B,,,true\n",
    )
    load_producers(db_session, csv_path=path)

    path.write_text(
        "name,format_id,default_origin,active\nTest Producer B,FMT-2,,true\n", encoding="utf-8"
    )
    count = load_producers(db_session, csv_path=path)

    assert count == 1
    rows = db_session.query(Producer).filter_by(name="Test Producer B").all()
    assert len(rows) == 1
    assert rows[0].format_id == "FMT-2"


def test_load_producers_blank_price_columns_do_not_wipe_existing_prices(db_session, tmp_path):
    path = _write_csv(
        tmp_path,
        "producers.csv",
        "name,format_id,default_origin,active,precio_caja_carbon,precio_transporte\n"
        "Test Priced Producer,,,true,100,200\n",
    )
    load_producers(db_session, csv_path=path)
    row = db_session.query(Producer).filter_by(name="Test Priced Producer").one()
    assert row.precio_caja_carbon == 100.0
    assert row.precio_transporte == 200.0

    path.write_text(
        "name,format_id,default_origin,active\nTest Priced Producer,,,true\n",
        encoding="utf-8",
    )
    load_producers(db_session, csv_path=path)
    row = db_session.query(Producer).filter_by(name="Test Priced Producer").one()
    assert row.precio_caja_carbon == 100.0
    assert row.precio_transporte == 200.0


def test_load_producers_malformed_row_missing_name_raises_keyerror(db_session, tmp_path):
    path = _write_csv(tmp_path, "producers.csv", "format_id,active\nFMT-1,true\n")

    with pytest.raises(KeyError):
        load_producers(db_session, csv_path=path)


# --- load_transportistas -------------------------------------------------


def test_load_transportistas_creates_canonical_and_aliases(db_session, tmp_path):
    path = _write_csv(
        tmp_path,
        "roster.csv",
        "canonical_name,alias,active\n"
        "TEST ABREGO O TRANSPORTES MAU,TEST ABREGO O TRANSPORTES MAU,true\n"
        "TEST ABREGO O TRANSPORTES MAU,TEST TRANSPORTES MAU,true\n",
    )

    count = load_transportistas(db_session, csv_path=path)

    assert count == 2
    t = db_session.query(Transportista).filter_by(canonical_name="TEST ABREGO O TRANSPORTES MAU").one()
    aliases = db_session.query(TransportistaAlias).filter_by(transportista_id=t.id).all()
    assert len(aliases) == 2
    assert {a.alias_text for a in aliases} == {
        "TEST ABREGO O TRANSPORTES MAU",
        "TEST TRANSPORTES MAU",
    }


def test_load_transportistas_alias_normalizes_phone_number(db_session, tmp_path):
    path = _write_csv(
        tmp_path,
        "roster.csv",
        "canonical_name,alias,active\nTEST POLO RODRIGUEZ,TEST POLO RODRIGUEZ (8641006935),true\n",
    )

    load_transportistas(db_session, csv_path=path)

    alias = db_session.query(TransportistaAlias).filter_by(alias_text="TEST POLO RODRIGUEZ").one_or_none()
    assert alias is not None
    assert alias.raw_alias_text == "TEST POLO RODRIGUEZ (8641006935)"


def test_load_transportistas_idempotent_rerun_no_duplicates(db_session, tmp_path):
    path = _write_csv(
        tmp_path,
        "roster.csv",
        "canonical_name,alias,active\nTEST CAMAGO,TEST camago,true\n",
    )

    load_transportistas(db_session, csv_path=path)
    load_transportistas(db_session, csv_path=path)

    canonicals = db_session.query(Transportista).filter_by(canonical_name="TEST CAMAGO").all()
    aliases = db_session.query(TransportistaAlias).filter_by(alias_text="TEST camago").all()
    assert len(canonicals) == 1
    assert len(aliases) == 1


def test_load_transportistas_conflicting_alias_warns_not_raises(db_session, tmp_path):
    path = _write_csv(
        tmp_path,
        "roster.csv",
        "canonical_name,alias,active\n"
        "TEST DRIVER ONE,TEST SHARED ALIAS,true\n"
        "TEST DRIVER TWO,TEST SHARED ALIAS,true\n",
    )

    with pytest.warns(UserWarning):
        count = load_transportistas(db_session, csv_path=path)

    assert count == 2
    alias = db_session.query(TransportistaAlias).filter_by(alias_text="TEST SHARED ALIAS").one()
    owner = db_session.get(Transportista, alias.transportista_id)
    assert owner.canonical_name == "TEST DRIVER ONE"  # first-seen wins


def test_load_transportistas_malformed_row_missing_alias_raises_keyerror(db_session, tmp_path):
    path = _write_csv(tmp_path, "roster.csv", "canonical_name,active\nTEST DRIVER,true\n")

    with pytest.raises(KeyError):
        load_transportistas(db_session, csv_path=path)


# --- load_pricing_rules / get_active_pricing_rules ------------------------


def test_load_pricing_rules_creates_and_upserts_by_rule_id(db_session, tmp_path):
    path = _write_csv(
        tmp_path,
        "pricing.csv",
        "rule_id,scope,pricing_mode,rate,currency,effective_from,effective_to,active\n"
        "TP001,Test Scope,flat,100.0,MXN,2024-01-01,,true\n",
    )
    load_pricing_rules(db_session, csv_path=path)

    path.write_text(
        "rule_id,scope,pricing_mode,rate,currency,effective_from,effective_to,active\n"
        "TP001,Test Scope,flat,150.0,MXN,2024-01-01,,true\n",
        encoding="utf-8",
    )
    count = load_pricing_rules(db_session, csv_path=path)

    assert count == 1
    rows = db_session.query(PricingRule).filter_by(rule_id="TP001").all()
    assert len(rows) == 1
    assert rows[0].rate == 150.0


def test_load_pricing_rules_malformed_row_missing_rate_raises_valueerror(db_session, tmp_path):
    path = _write_csv(
        tmp_path,
        "pricing.csv",
        "rule_id,scope,pricing_mode,rate,effective_from\nTP002,Test Scope,flat,,2024-01-01\n",
    )

    with pytest.raises(ValueError):
        load_pricing_rules(db_session, csv_path=path)


def test_get_active_pricing_rules_excludes_past_effective_to(db_session, tmp_path):
    path = _write_csv(
        tmp_path,
        "pricing.csv",
        "rule_id,scope,pricing_mode,rate,effective_from,effective_to\n"
        "TP010,Test Scope,flat,100.0,2020-01-01,2020-12-31\n",
    )
    load_pricing_rules(db_session, csv_path=path)

    active = get_active_pricing_rules(db_session, as_of="2025-01-01")

    assert "TP010" not in {r.rule_id for r in active}


def test_get_active_pricing_rules_includes_null_effective_to(db_session, tmp_path):
    path = _write_csv(
        tmp_path,
        "pricing.csv",
        "rule_id,scope,pricing_mode,rate,effective_from,effective_to\n"
        "TP011,Test Scope,flat,100.0,2020-01-01,\n",
    )
    load_pricing_rules(db_session, csv_path=path)

    active = get_active_pricing_rules(db_session, as_of="2025-01-01")

    assert "TP011" in {r.rule_id for r in active}


def test_get_active_pricing_rules_respects_active_flag_independently_of_dates(db_session, tmp_path):
    path = _write_csv(
        tmp_path,
        "pricing.csv",
        "rule_id,scope,pricing_mode,rate,effective_from,effective_to,active\n"
        "TP012,Test Scope,flat,100.0,2020-01-01,,false\n",
    )
    load_pricing_rules(db_session, csv_path=path)

    active = get_active_pricing_rules(db_session, as_of="2025-01-01")

    assert "TP012" not in {r.rule_id for r in active}
