"""Central runtime configuration, loaded from environment / .env.

All values have sane local-dev defaults so the app runs with zero setup.
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root, e.g. /home/user/boletas_energiamineral
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'boletas.db'}"
    originals_dir: Path = BASE_DIR / "data" / "originals"
    ocr_language: str = "spa+eng"
    rules_config_dir: Path = BASE_DIR / "app" / "rules"
    auto_process_confidence_min: float = 0.75

    # OCR backend selection.
    #   "tesseract" -> local Tesseract only (offline, no API key).
    #   "openai"    -> OpenAI vision model only (best on handwriting/photos).
    #   "auto"      -> Tesseract first; if a key is configured and Tesseract's
    #                  confidence is below ocr_fallback_min_confidence, re-OCR
    #                  with OpenAI. Falls back to Tesseract if OpenAI errors or
    #                  no key is set, so "auto" is always safe.
    ocr_backend: str = "auto"
    openai_api_key: str | None = None
    openai_ocr_model: str = "gpt-4o-mini"
    # Tesseract overall confidence (0-100) below which "auto" escalates to OpenAI.
    ocr_fallback_min_confidence: float = 70.0

    # Supabase Auth (admin login) + Postgres (set database_url above to a
    # Supabase Postgres connection string to actually use it as the DB;
    # these three are used purely for the Auth REST calls in app/auth/).
    supabase_url: str | None = None
    supabase_anon_key: str | None = None
    supabase_service_role_key: str | None = None

    # Signs the admin session cookie. Has an insecure local-dev default so
    # the app still runs with zero setup -- MUST be overridden via env var
    # (a long random value) in any real deployment.
    session_secret_key: str = "dev-only-insecure-secret-change-me"

    # Public origin Twilio uses to hit this app (https://host, no trailing
    # path). Needed so X-Twilio-Signature validates when the process sits
    # behind a reverse proxy that rewrites the scheme/host (Railway, etc.).
    # If unset, the webhook reconstructs the URL from X-Forwarded-* headers.
    public_base_url: str | None = None

    # Twilio WhatsApp inbound (POST /webhooks/twilio/whatsapp). The webhook
    # is unauthenticated on purpose -- Twilio signs each request; we reject
    # anything whose X-Twilio-Signature does not match twilio_auth_token.
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_whatsapp_from: str | None = None  # e.g. whatsapp:+14155238886 (sandbox)
    # Comma-separated E.164 numbers allowed to upload. Empty = accept any
    # sender (fine for the invite-only sandbox; set this in production).
    whatsapp_allowed_senders: str = ""

    def ensure_dirs(self) -> None:
        self.originals_dir.mkdir(parents=True, exist_ok=True)
        (BASE_DIR / "data").mkdir(parents=True, exist_ok=True)


settings = Settings()
