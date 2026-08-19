"""OpenAI OCR adapter + backend selection. No real API calls -- the OpenAI
client is monkeypatched so these run offline/deterministically."""
from __future__ import annotations

import json
import types

import pytest

from app.ocr.base import OCRResult
from app.ocr.factory import FallbackOCRAdapter, get_ocr_adapter
from app.ocr.openai_adapter import OpenAIError, OpenAIOCRAdapter
from app.ocr.tesseract_adapter import TesseractOCRAdapter
from app.parsing.field_parser import parse_fields


def _fake_openai(monkeypatch, *, content: str | None = None, raise_exc: Exception | None = None):
    """Installs a fake `openai.OpenAI` whose chat.completions.create returns
    `content` (or raises `raise_exc`). Returns a dict recording the call."""
    calls: dict = {}

    class _Completions:
        def create(self, **kwargs):
            calls.update(kwargs)
            if raise_exc is not None:
                raise raise_exc
            message = types.SimpleNamespace(content=content)
            choice = types.SimpleNamespace(message=message)
            return types.SimpleNamespace(choices=[choice])

    class _Client:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=_Completions())

    import openai

    monkeypatch.setattr(openai, "OpenAI", _Client)
    return calls


def _img(tmp_path):
    p = tmp_path / "boleta.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n fake image bytes")
    return p


def test_openai_adapter_returns_transcription_that_parses(tmp_path, monkeypatch):
    transcription = (
        "Folio: B-1000\n"
        "Fecha: 19/Agosto/2026\n"
        "Destino: C.T. Jose Lopez Portillo\n"
        "Datos del chofer del camion: Luis Perez\n"
        "Centro de Explotacion: Tajo San Jose\n"
        "Volumen Entregado: 900\n"
    )
    _fake_openai(monkeypatch, content=json.dumps({"transcription": transcription, "confidence": 93}))

    result = OpenAIOCRAdapter(api_key="sk-test").extract(_img(tmp_path))

    assert result.confidence == 93.0
    assert result.engine == "openai:gpt-4o-mini"  # so the UI/record can show which backend ran
    assert "Luis Perez" in result.text

    parsed = parse_fields(result)
    assert parsed.folio == "B-1000"
    assert parsed.date == "2026-08-19"  # spelled-out month, read by the LLM
    assert parsed.destination == "C.T. Jose Lopez Portillo"
    assert parsed.fletero == "Luis Perez"
    assert parsed.origin == "Tajo San Jose"
    assert parsed.weight == 900.0


def test_openai_adapter_sends_model_and_image(tmp_path, monkeypatch):
    calls = _fake_openai(monkeypatch, content=json.dumps({"transcription": "Folio: X", "confidence": 50}))

    OpenAIOCRAdapter(api_key="sk-test", model="gpt-4o-mini").extract(_img(tmp_path))

    assert calls["model"] == "gpt-4o-mini"
    # A base64 data URL image part is included in the request.
    user_msg = next(m for m in calls["messages"] if m["role"] == "user")
    image_part = next(part for part in user_msg["content"] if part["type"] == "image_url")
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")


def test_openai_adapter_without_key_raises(tmp_path, monkeypatch):
    import app.config as app_config

    monkeypatch.setattr(app_config.settings, "openai_api_key", None)
    with pytest.raises(OpenAIError):
        OpenAIOCRAdapter(api_key=None).extract(_img(tmp_path))


def test_openai_adapter_wraps_api_errors(tmp_path, monkeypatch):
    _fake_openai(monkeypatch, raise_exc=RuntimeError("boom"))
    with pytest.raises(OpenAIError):
        OpenAIOCRAdapter(api_key="sk-test").extract(_img(tmp_path))


class _StubAdapter:
    def __init__(self, text: str, confidence: float, engine: str = "stub"):
        self._result = OCRResult(text=text, confidence=confidence, words=[], engine=engine)
        self.called = False

    def extract(self, image_path):
        self.called = True
        return self._result


class _RaisingAdapter:
    def extract(self, image_path):
        raise OpenAIError("unavailable")


def test_fallback_keeps_primary_when_confident(tmp_path):
    primary = _StubAdapter("primary text", 90.0)
    fallback = _StubAdapter("fallback text", 99.0)
    adapter = FallbackOCRAdapter(primary, fallback, min_confidence=70.0)

    result = adapter.extract(_img(tmp_path))

    assert result.text == "primary text"
    assert fallback.called is False


def test_fallback_escalates_when_primary_low_confidence(tmp_path):
    primary = _StubAdapter("garbled", 20.0, engine="tesseract")
    fallback = _StubAdapter("clean transcription", 95.0, engine="openai:gpt-4o-mini")
    adapter = FallbackOCRAdapter(primary, fallback, min_confidence=70.0)

    result = adapter.extract(_img(tmp_path))

    assert result.text == "clean transcription"
    assert result.engine == "openai:gpt-4o-mini"  # record reflects the engine actually used
    assert fallback.called is True


def test_fallback_swallows_fallback_error(tmp_path):
    primary = _StubAdapter("primary text", 20.0)
    adapter = FallbackOCRAdapter(primary, _RaisingAdapter(), min_confidence=70.0)

    result = adapter.extract(_img(tmp_path))

    assert result.text == "primary text"  # fallback failure never breaks the scan


@pytest.mark.parametrize(
    "backend,has_key,expected",
    [
        ("tesseract", True, TesseractOCRAdapter),
        ("tesseract", False, TesseractOCRAdapter),
        ("openai", True, OpenAIOCRAdapter),
        ("openai", False, TesseractOCRAdapter),  # no key -> safe fallback
        ("auto", True, FallbackOCRAdapter),
        ("auto", False, TesseractOCRAdapter),
    ],
)
def test_get_ocr_adapter_selects_backend(monkeypatch, backend, has_key, expected):
    import app.config as app_config

    monkeypatch.setattr(app_config.settings, "ocr_backend", backend)
    monkeypatch.setattr(app_config.settings, "openai_api_key", "sk-test" if has_key else None)

    assert isinstance(get_ocr_adapter(), expected)
