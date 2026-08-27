"""Twilio request signature checks. Always use the SDK validator."""
from __future__ import annotations

from fastapi import Request
from twilio.request_validator import RequestValidator

from app.config import settings


def webhook_url(request: Request) -> str:
    """Absolute URL Twilio signed. Prefer PUBLIC_BASE_URL, then forwarded
    proto/host, then the raw request URL (works for TestClient)."""
    path = request.url.path
    query = f"?{request.url.query}" if request.url.query else ""
    configured = (settings.public_base_url or "").rstrip("/")
    if configured:
        return f"{configured}{path}{query}"
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    # X-Forwarded-Proto can be a comma list ("https,http"); take the first.
    proto = proto.split(",")[0].strip()
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    )
    host = host.split(",")[0].strip()
    return f"{proto}://{host}{path}{query}"


def signature_is_valid(request: Request, params: dict[str, str], signature: str) -> bool:
    token = (settings.twilio_auth_token or "").strip()
    if not token or not signature:
        return False
    validator = RequestValidator(token)
    return validator.validate(webhook_url(request), params, signature)
