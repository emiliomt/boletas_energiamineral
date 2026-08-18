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

    def ensure_dirs(self) -> None:
        self.originals_dir.mkdir(parents=True, exist_ok=True)
        (BASE_DIR / "data").mkdir(parents=True, exist_ok=True)


settings = Settings()
