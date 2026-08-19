"""Verifies admin credentials against Supabase Auth's REST API.

No custom password storage/hashing in this app at all -- Supabase Auth is
the sole identity provider. There is intentionally no signup route; the
one admin account is created out-of-band via scripts/create_admin_user.py.
"""
from __future__ import annotations

import httpx

from app.config import settings


class SupabaseNotConfigured(RuntimeError):
    """Raised when a login is attempted before SUPABASE_URL/ANON_KEY are set."""


def verify_credentials(email: str, password: str) -> dict | None:
    """Returns the Supabase user payload on success, None on invalid
    credentials. Raises SupabaseNotConfigured if Supabase isn't set up."""
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise SupabaseNotConfigured(
            "SUPABASE_URL and SUPABASE_ANON_KEY must be set to log in (see .env.example)."
        )

    response = httpx.post(
        f"{settings.supabase_url}/auth/v1/token",
        params={"grant_type": "password"},
        json={"email": email, "password": password},
        headers={"apikey": settings.supabase_anon_key, "Content-Type": "application/json"},
        timeout=10.0,
    )
    if response.status_code != 200:
        return None
    return response.json()
