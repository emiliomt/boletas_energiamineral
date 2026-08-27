"""Download inbound WhatsApp/MMS media from Twilio's MediaUrl."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

ACCEPTED_MIME_TYPES = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/tiff": "tiff",
    "application/pdf": "pdf",
}

# Stay well under Twilio's 15s webhook read timeout.
DOWNLOAD_TIMEOUT_S = 8.0


@dataclass(frozen=True)
class DownloadedMedia:
    filename: str
    content: bytes
    mime_type: str


def _extension(mime_type: str) -> str | None:
    return ACCEPTED_MIME_TYPES.get((mime_type or "").split(";")[0].strip().lower())


def parse_media_items(params: dict[str, str]) -> list[tuple[str, str]]:
    """Return (url, content_type) pairs from Twilio form params."""
    try:
        num_media = int(params.get("NumMedia") or "0")
    except ValueError:
        num_media = 0
    items: list[tuple[str, str]] = []
    for index in range(num_media):
        url = (params.get(f"MediaUrl{index}") or "").strip()
        content_type = (params.get(f"MediaContentType{index}") or "").strip()
        if url:
            items.append((url, content_type))
    return items


def download_media(url: str, content_type: str, filename_stem: str) -> DownloadedMedia | str:
    """Fetch one Twilio media URL. Returns DownloadedMedia or an error string.

    Twilio media URLs require HTTP Basic (Account SID + Auth Token) on the
    first hop; they 302 to a signed S3 URL that must be fetched *without*
    forwarding that Authorization header (httpx does this on cross-host
    redirects).
    """
    sid = (settings.twilio_account_sid or "").strip()
    token = (settings.twilio_auth_token or "").strip()
    if not sid or not token:
        return "WhatsApp no está configurado (faltan credenciales de Twilio)."

    declared_mime = (content_type or "").split(";")[0].strip().lower()
    if declared_mime and declared_mime not in ACCEPTED_MIME_TYPES:
        return (
            f"No puedo procesar archivos {declared_mime}. "
            "Mandá una foto (JPG/PNG) o un PDF de la boleta."
        )

    try:
        response = httpx.get(
            url,
            auth=(sid, token),
            follow_redirects=True,
            timeout=DOWNLOAD_TIMEOUT_S,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        logger.exception("Failed to download Twilio media %s", url)
        return "No pude descargar la foto desde WhatsApp. Mandala de nuevo."

    header_mime = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
    mime = header_mime if _extension(header_mime) else (declared_mime or "image/jpeg")
    if mime == "image/jpg":
        mime = "image/jpeg"
    ext = _extension(mime)
    if ext is None:
        return (
            f"No puedo procesar archivos {mime or 'desconocido'}. "
            "Mandá una foto (JPG/PNG) o un PDF de la boleta."
        )
    return DownloadedMedia(
        filename=f"{filename_stem}.{ext}",
        content=response.content,
        mime_type=mime,
    )
