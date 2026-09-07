"""Saved 'Datos de la boleta' templates for the folio-batch creation form."""
from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from tests.test_folio_batch_generation import ONLINE_FIELDS


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.config as app_config

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(app_config.settings, "database_url", f"sqlite:///{db_path}")
    monkeypatch.setattr(app_config.settings, "originals_dir", tmp_path / "originals")

    import app.db as app_db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(app_db, "engine", test_engine)
    monkeypatch.setattr(app_db, "SessionLocal", sessionmaker(bind=test_engine, autoflush=False, autocommit=False))

    from app.auth.session import require_admin_api, require_admin_web
    from app.main import app

    app.dependency_overrides[require_admin_api] = lambda: "test-admin@example.com"
    app.dependency_overrides[require_admin_web] = lambda: "test-admin@example.com"
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _save_template(client, name: str, fields: dict | None = None):
    data = {"template_name": name, "label": "ignored", "mode": "sequential", **(fields or ONLINE_FIELDS)}
    return client.post("/admin/folio-batches/templates", data=data, follow_redirects=False)


def _templates_json(html: str) -> dict:
    match = re.search(r'id="boleta-templates-data">(.*?)</script>', html, re.S)
    assert match is not None
    return json.loads(match.group(1))


def test_create_template_persists_boleta_fields(client):
    resp = _save_template(client, "CFE López Portillo")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/folio-batches"

    html = client.get("/admin/folio-batches").text
    assert "CFE López Portillo" in html
    payload = _templates_json(html)
    assert len(payload) == 1
    values = next(iter(payload.values()))
    for field, expected in ONLINE_FIELDS.items():
        assert values[field] == expected, field


def test_save_same_name_updates_template(client):
    _save_template(client, "Contrato A", {**ONLINE_FIELDS, "destino": "Patio 1"})
    _save_template(client, "Contrato A", {**ONLINE_FIELDS, "destino": "Patio 2"})

    html = client.get("/admin/folio-batches").text
    assert html.count("Contrato A") >= 1
    values = next(iter(_templates_json(html).values()))
    assert values["destino"] == "Patio 2"
    assert "Patio 1" not in values["destino"]


def test_delete_template(client):
    _save_template(client, "Para borrar")
    html = client.get("/admin/folio-batches").text
    template_id = next(iter(_templates_json(html).keys()))

    resp = client.post(
        "/admin/folio-batches/templates/delete",
        data={"ids": template_id},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    html = client.get("/admin/folio-batches").text
    assert "Para borrar" not in html
    assert _templates_json(html) == {}


def test_form_exposes_unlocked_fields_and_prefill_payload(client):
    _save_template(client, "Prefill")
    html = client.get("/admin/folio-batches").text

    assert 'id="boleta-template-select"' in html
    assert "Guardar como plantilla" in html
    assert 'name="proveedor"' in html
    # Template is a prefill, not a lock — inputs stay editable.
    proveedor_tag = re.search(r"<input[^>]*name=\"proveedor\"[^>]*>", html).group(0)
    assert "disabled" not in proveedor_tag
    assert "readonly" not in proveedor_tag

    payload = _templates_json(html)
    values = next(iter(payload.values()))
    assert values["contrato"] == ONLINE_FIELDS["contrato"]
    assert values["centro_explotacion"] == ONLINE_FIELDS["centro_explotacion"]


def test_empty_template_name_is_rejected(client):
    resp = _save_template(client, "  ")
    assert resp.status_code == 200
    assert "nombre para guardar la plantilla" in resp.text


def test_creating_a_lote_still_works_with_template_fields_present(client):
    resp = client.post(
        "/admin/folio-batches",
        data={
            "label": "Semana 50",
            "mode": "sequential",
            "prefix": "T-",
            "start_number": 1,
            "count": 1,
            "template_name": "should-be-ignored",
            **ONLINE_FIELDS,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "/admin/folio-batches/" in resp.headers["location"]

    batches = client.get("/api/folio-batches").json()
    batch = next(b for b in batches if b["label"] == "Semana 50")
    for field, value in ONLINE_FIELDS.items():
        assert batch[field] == value, field
