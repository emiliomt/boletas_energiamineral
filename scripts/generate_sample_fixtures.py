#!/usr/bin/env python3
"""Synthesizes 3 sample boleta PNGs into tests/fixtures/boletas/, so tests
and manual verification runs don't depend on real client scans.

1. sample_boleta_01.png     — clean, all fields present, matches route R001.
2. sample_boleta_02.png     — same route family, weight blank (estimated).
3. sample_boleta_03_illegible.png — garbled/low-contrast text (needs review).

Usage: python scripts/generate_sample_fixtures.py
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "boletas"


def _font(size: int = 22):
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _render_lines(lines: list[str], path: Path, noisy: bool = False) -> None:
    img = Image.new("RGB", (900, 500), color="white")
    draw = ImageDraw.Draw(img)
    font = _font()
    y = 30
    for line in lines:
        draw.text((40, y), line, fill="black", font=font)
        y += 45

    if noisy:
        # Simulate a low-quality / hard-to-read scan: scatter noise pixels
        # and dark speckle over the text so OCR confidence stays low.
        rand = random.Random(42)
        pixels = img.load()
        for _ in range(45000):
            x = rand.randrange(0, img.width)
            y2 = rand.randrange(0, img.height)
            pixels[x, y2] = (rand.randrange(120, 200),) * 3

    img.save(path)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    _render_lines(
        [
            "Folio: B-2001",
            "Fecha: 20/01/2026",
            "Centro de Explotacion: Mina San Jose",
            "Destino: Planta Norte",
            "Datos del chofer del camion: Juan Perez",
            "Volumen por Entregar: 9200",
            "Volumen Entregado: 9200 kg",
        ],
        OUT_DIR / "sample_boleta_01.png",
    )

    _render_lines(
        [
            "Folio: B-2002",
            "Fecha: 20/01/2026",
            "Centro de Explotacion: Planta Norte",
            "Destino: Patio Almacen",
            "Datos del chofer del camion: Maria Lopez",
        ],
        OUT_DIR / "sample_boleta_02.png",
    )

    _render_lines(
        [
            "Fol10: 6-?0O3",
            "F3cha: ??/??/2026",
            "0r1gen: ~~~~~~",
            "D3st1n0: ~~~~~~",
        ],
        OUT_DIR / "sample_boleta_03_illegible.png",
        noisy=True,
    )

    print(f"Wrote 3 sample fixtures to {OUT_DIR}")


if __name__ == "__main__":
    main()
