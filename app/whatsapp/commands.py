"""Parse short Spanish commands sent as WhatsApp text (or photo captions)."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


HELP_TEXT = (
    "Mandá fotos de las boletas por este chat y las agrupo en un lote de escaneo. "
    "Cada foto entra al lote actual; cuando termines, escribí fin.\n\n"
    "Comandos:\n"
    "• ayuda — esta ayuda\n"
    "• lote nuevo [nombre] — abrir un lote nuevo\n"
    "• lote 12 — usar el lote de escaneo #12\n"
    "• lote Camión Norte — usar o crear un lote con ese nombre\n"
    "• tipo salida | tipo entrada — tipo del lote actual\n"
    "• productor NOMBRE — productor (solo Entrada)\n"
    "• cfe — la próxima foto es comprobante CFE\n"
    "• boleta — la próxima foto es boleta (por defecto)\n"
    "• estado — lote actual\n"
    "• fin — cerrar el lote (las siguientes fotos abren otro)"
)


@dataclass(frozen=True)
class Command:
    kind: str
    arg: str | None = None


def _fold(text: str) -> str:
    """Lowercase ASCII fold so 'Ayúda' / 'CERRAR' still match."""
    normalized = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return stripped.lower().strip()


def parse_command(body: str) -> Command | None:
    """Return a Command if `body` is an explicit operator message.

    Random captions on photos (`boleta de Juan`) return None so the photo
    still uploads. Only prefixed/keyword commands match.
    """
    text = (body or "").strip()
    if not text:
        return None
    folded = _fold(text)

    if folded in {"ayuda", "help", "hola", "start", "menu", "hi"}:
        return Command("help")
    if folded in {"estado", "status"}:
        return Command("status")
    if folded in {"fin", "cerrar", "listo"}:
        return Command("close")
    if folded in {"cfe", "comprobante", "ticket cfe", "comprobante cfe"}:
        return Command("doc_type", "cfe_slip")
    if folded == "boleta":
        return Command("doc_type", "boleta")
    if folded in {"entrada", "tipo entrada"}:
        return Command("set_kind", "entrada")
    if folded in {"salida", "tipo salida"}:
        return Command("set_kind", "salida")

    tipo_match = re.fullmatch(r"tipo\s+(entrada|salida)", folded)
    if tipo_match:
        return Command("set_kind", tipo_match.group(1))

    if folded.startswith("productor ") or folded.startswith("proveedor "):
        arg = text.split(None, 1)[1].strip()
        return Command("set_producer", arg) if arg else None

    if folded == "lote" or folded == "lote nuevo":
        return Command("new_lote", None)
    if folded.startswith("lote nuevo"):
        arg = text.split(None, 2)
        # "lote nuevo" or "lote nuevo Turno noche"
        name = arg[2].strip() if len(arg) >= 3 else None
        return Command("new_lote", name or None)
    if folded.startswith("lote "):
        arg = text.split(None, 1)[1].strip()
        if not arg:
            return Command("new_lote", None)
        if arg.startswith("#"):
            arg = arg[1:].strip()
        return Command("bind_lote", arg)

    return None
