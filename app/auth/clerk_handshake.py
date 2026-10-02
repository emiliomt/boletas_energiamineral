"""Clerk document handshake.

clerk-js keeps the live session on the Frontend API host. It does not write
an app-domain ``__session`` cookie until a browser navigation is redirected
through ``/v1/client/handshake`` and the return payload's Set-Cookie
directives are applied. Without that step, a successful sign-in is sent to
the app, ``require_admin_web`` sees no session, and the browser is bounced
to ``/login``, where the signed-in widget immediately leaves again.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import Request

from app.config import settings

logger = logging.getLogger(__name__)

HANDSHAKE_API_VERSION = "2026-05-12"
REDIRECT_COUNT_COOKIE = "__clerk_redirect_count"
MAX_HANDSHAKES = 3

_STALE_SESSION_REASONS = {
    "token-expired",
    "TOKEN_EXPIRED",
    "token-not-active-yet",
    "TOKEN_NOT_ACTIVE_YET",
    "token-iat-in-the-future",
    "TOKEN_IAT_IN_THE_FUTURE",
}
_SKIP_REASONS = {"clerk_sdk_missing", "clerk_not_configured"}
_CLERK_QUERY_PARAMS = {
    "__clerk_handshake",
    "__clerk_handshake_nonce",
    "__clerk_db_jwt",
    "__clerk_help",
    "__dev_session",
    "__clerk_synced",
    "__clerk_redirect_url",
}
_AUTH_PATHS = {"/login", "/logout"}


@dataclass
class HandshakeAction:
    location: str
    set_cookies: list[str]


class AuthHandshake(Exception):
    """Browser must complete the Clerk handshake before the session exists."""

    def __init__(self, location: str, set_cookies: list[str] | None = None):
        self.location = location
        self.set_cookies = list(set_cookies or [])
        super().__init__(location)


def safe_next_path(value: str | None, default: str = "/") -> str:
    """In-site path safe to use as a post-login destination.

    Rejects absolute URLs, protocol-relative paths, and ``/login`` / ``/logout``
    so a signed-in Clerk widget cannot redirect at the login page itself.
    """
    if not isinstance(value, str):
        return default
    candidate = value.strip()
    if not candidate.startswith("/") or candidate.startswith("//") or candidate.startswith("/\\"):
        return default
    if "\\" in candidate or any(ord(ch) < 32 for ch in candidate):
        return default
    path_only = candidate.split("?", 1)[0].split("#", 1)[0]
    normalized = path_only.rstrip("/") or "/"
    if normalized in _AUTH_PATHS or normalized.startswith("/login/") or normalized.startswith("/logout/"):
        return default
    return candidate


def is_document_navigation(request: Request) -> bool:
    """True for browser page loads. Fetch/XHR must not be handshake-redirected."""
    if request.method.upper() != "GET":
        return False
    dest = (request.headers.get("sec-fetch-dest") or "").strip().lower()
    if dest in {"document", "iframe"}:
        return True
    if dest:
        return False
    accept = (request.headers.get("accept") or "").strip().lower()
    return accept.startswith("text/html")


def _cookie(request: Request, name: str) -> str | None:
    value = request.cookies.get(name)
    if value is not None:
        return value
    prefix = name + "_"
    for key, item in request.cookies.items():
        if key.startswith(prefix):
            return item
    return None


def _client_uat(request: Request) -> int:
    raw = _cookie(request, "__client_uat")
    if raw is None:
        return 0
    try:
        return int(str(raw).strip().strip('"'))
    except ValueError:
        return 0


def _has_session_cookie(request: Request) -> bool:
    if request.cookies.get("__session"):
        return True
    return any(name.startswith("__session") for name in request.cookies)


def _redirect_count(request: Request) -> int:
    raw = request.cookies.get(REDIRECT_COUNT_COOKIE)
    try:
        return int(raw) if raw is not None else 0
    except ValueError:
        return 0


def _redirect_count_directive(count: int) -> str:
    parts = [
        f"{REDIRECT_COUNT_COOKIE}={count}",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        "Max-Age=2",
    ]
    if settings.is_production:
        parts.append("Secure")
    return "; ".join(parts)


def _handshake_enabled() -> bool:
    return bool((settings.clerk_secret_key or "").strip() and settings.clerk_frontend_api_host)


def _public_url(request: Request) -> str:
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    forwarded_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    proto = forwarded_proto or request.url.scheme or "https"
    host = forwarded_host or (request.headers.get("host") or "").strip() or request.url.netloc
    if proto not in {"http", "https"} or not host or any(ch in host for ch in " /@\\"):
        proto = request.url.scheme or "https"
        host = request.url.netloc
    path = request.url.path or "/"
    query = request.url.query
    url = f"{proto}://{host}{path}"
    if query:
        url += "?" + query
    return url


def _without_clerk_params(url: str) -> str:
    parts = urlsplit(url)
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in _CLERK_QUERY_PARAMS]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


def _relative_target(request: Request) -> str:
    cleaned = _without_clerk_params(_public_url(request))
    parts = urlsplit(cleaned)
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    return safe_next_path(target)


def handshake_nonce(request: Request) -> str | None:
    # Query only. A leftover handshake cookie would 307 to the same URL forever.
    return request.query_params.get("__clerk_handshake_nonce")


def handshake_token(request: Request) -> str | None:
    return request.query_params.get("__clerk_handshake")


def fetch_handshake_directives(nonce: str) -> list[str]:
    """Exchange a handshake nonce for Set-Cookie directives from Clerk."""
    secret = (settings.clerk_secret_key or "").strip()
    if not secret or not nonce:
        return []
    response = httpx.get(
        "https://api.clerk.com/v1/clients/handshake_payload",
        params={"nonce": nonce},
        headers={
            "Authorization": f"Bearer {secret}",
            "Accept": "application/json",
        },
        timeout=10.0,
    )
    response.raise_for_status()
    payload = response.json()
    raw = payload.get("directives") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []
    return _safe_directives(raw)


def _safe_directives(items: list) -> list[str]:
    directives: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            continue
        if "\n" in item or "\r" in item or "\x00" in item:
            continue
        directives.append(item.strip())
    return directives


def build_handshake_redirect(request: Request, reason: str) -> HandshakeAction:
    host = settings.clerk_frontend_api_host
    redirect_url = _without_clerk_params(_public_url(request))
    params = {
        "redirect_url": redirect_url,
        "__clerk_api_version": HANDSHAKE_API_VERSION,
        "suffixed_cookies": "false",
        "__clerk_hs_reason": reason,
        "format": "nonce",
    }
    dev_browser = _cookie(request, "__clerk_db_jwt")
    if dev_browser and (settings.clerk_publishable_key or "").startswith("pk_test_"):
        params["__clerk_db_jwt"] = dev_browser
    location = f"https://{host}/v1/client/handshake?{urlencode(params)}"
    count = _redirect_count(request) + 1
    return HandshakeAction(location=location, set_cookies=[_redirect_count_directive(count)])


def maybe_handshake(request: Request, failure_reason: str | None) -> HandshakeAction | None:
    """Redirect a signed-in browser that still has no usable app session cookie."""
    if failure_reason in _SKIP_REASONS or not _handshake_enabled() or not is_document_navigation(request):
        return None
    if handshake_nonce(request) or handshake_token(request):
        return None
    if _redirect_count(request) >= MAX_HANDSHAKES:
        logger.info("Clerk handshake stopped after %s redirects", MAX_HANDSHAKES)
        return None
    uat = _client_uat(request)
    has_session = _has_session_cookie(request)
    stale = failure_reason in _STALE_SESSION_REASONS
    if uat > 0 and (not has_session or stale):
        reason = "client-uat-without-session-token" if not has_session else "session-token-expired-or-stale"
        return build_handshake_redirect(request, reason)
    if has_session and stale:
        return build_handshake_redirect(request, "session-token-expired-or-stale")
    return None


def resolve_handshake_return(request: Request) -> HandshakeAction | None:
    """Apply cookies from a handshake return, then redirect to the clean URL."""
    if request.method.upper() != "GET":
        return None
    nonce = handshake_nonce(request)
    token = handshake_token(request)
    if not nonce and not token:
        return None
    directives: list[str] = []
    if nonce:
        try:
            directives = fetch_handshake_directives(nonce)
        except Exception:
            logger.exception("Clerk handshake payload request failed")
            directives = []
    elif token:
        directives = _directives_from_handshake_token(token)
    return HandshakeAction(location=_relative_target(request), set_cookies=directives)


# Handshake JWTs share the instance signing key with JWT templates. Only the
# protected-header ``cat`` distinguishes them. A template token must not be
# turned into Set-Cookie directives.
_SESSION_JWT_CATEGORY = "cl_B7d4PD111AAA"
_IGNORED_JWT_CATEGORY = "cl_I7d4PD111III"


def _directives_from_handshake_token(token: str) -> list[str]:
    """Verify a legacy ``__clerk_handshake`` JWT and return its cookie directives."""
    secret = (settings.clerk_secret_key or "").strip()
    jwt_key = (settings.clerk_jwt_key or "").strip() or None
    if not secret and not jwt_key:
        return []
    try:
        import jwt
        from clerk_backend_api.jwks_helpers.verifytoken import VerifyTokenOptions, get_remote_jwt_key
    except Exception:
        logger.exception("Clerk handshake token verification is unavailable")
        return []
    try:
        header = jwt.get_unverified_header(token)
    except Exception:
        logger.exception("Clerk handshake token header is not a JWT")
        return []
    category = header.get("cat") if isinstance(header, dict) else None
    if category not in (None, "", _SESSION_JWT_CATEGORY, _IGNORED_JWT_CATEGORY):
        logger.info("Rejected handshake token with non-session category")
        return []
    options = VerifyTokenOptions(secret_key=secret or None, jwt_key=jwt_key)
    try:
        if jwt_key:
            key = jwt_key
        else:
            key = get_remote_jwt_key(token, options)
        payload = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            options={"verify_aud": False, "verify_iss": False},
        )
    except Exception:
        logger.exception("Clerk handshake token could not be verified")
        return []
    raw = payload.get("handshake") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return []
    return _safe_directives(raw)
