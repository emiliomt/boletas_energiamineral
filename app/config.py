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
    # Environment: "production" | "development" | "test" (defaults to development)
    environment: str = "development"

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
    # Additional heuristic: even when overall confidence is high, escalate to
    # OpenAI when too many core fields are missing/low-confidence (handwriting).
    # Enabled by default; safe with no key (has no effect).
    ocr_field_fallback_enabled: bool = True
    # Field-confidence (0-1) below which a required field counts as "bad".
    ocr_field_fallback_field_conf_min: float = 0.5
    # Number of bad core fields (folio, origin, destination, fletero) to trigger.
    ocr_field_fallback_min_bad_fields: int = 2

    # Supabase Auth (admin login) + Postgres (set database_url above to a
    # Supabase Postgres connection string to actually use it as the DB;
    # these three are used purely for the Auth REST calls in app/auth/).
    supabase_url: str | None = None
    supabase_anon_key: str | None = None
    supabase_service_role_key: str | None = None

    # Clerk (authentication)
    # Publishable and secret keys must be provided via environment variables.
    # Never expose CLERK_SECRET_KEY to the client.
    clerk_publishable_key: str | None = None
    clerk_secret_key: str | None = None
    # Optional: PEM public key to verify session tokens locally without network.
    clerk_jwt_key: str | None = None
    # Optional: comma/space separated list of authorized parties (origins) to validate tokens against.
    clerk_authorized_parties: str | None = None

    # Signs the admin session cookie. Has an insecure local-dev default so
    # the app still runs with zero setup -- MUST be overridden via env var
    # (a long random value) in any real deployment.
    session_secret_key: str = "dev-only-insecure-secret-change-me"
    # Comma-separated list of admin emails allowed to log in (optional).
    # If empty, any Supabase-authenticated user can log in; in production,
    # deployments SHOULD set this to the single admin email.
    admin_emails: str | None = None

    def ensure_dirs(self) -> None:
        self.originals_dir.mkdir(parents=True, exist_ok=True)
        (BASE_DIR / "data").mkdir(parents=True, exist_ok=True)

    # ---- Derived helpers
    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_production(self) -> bool:
        """Detect production for security-sensitive defaults."""
        # Explicit flag wins.
        env = (self.environment or "").strip().lower()
        if env in {"prod", "production"}:
            return True
        if env in {"dev", "development", "local", "test"}:
            return False
        # Railway deployments export RAILWAY* envs; treat presence as prod.
        import os
        if os.environ.get("RAILWAY_STATIC_URL") or os.environ.get("RAILWAY_ENVIRONMENT"):
            return True
        # Non-SQLite connection string is most likely a real DB -> prod-ish.
        return not self.is_sqlite

    @property
    def admin_allowlist(self) -> set[str]:
        if not self.admin_emails:
            return set()
        # Accept comma or whitespace separated; normalize to lowercase.
        parts = [p.strip().lower() for p in self.admin_emails.replace(" ", ",").split(",")]
        return {p for p in parts if p}

    @property
    def clerk_authorized_parties_list(self) -> list[str]:
        if not self.clerk_authorized_parties:
            return []
        parts = [p.strip() for p in self.clerk_authorized_parties.replace(" ", ",").split(",")]
        return [p for p in parts if p]


settings = Settings()
