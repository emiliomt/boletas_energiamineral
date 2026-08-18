"""Splits a multi-page PDF into one PNG per page (via pdf2image/poppler)."""
from __future__ import annotations

from pathlib import Path

from pdf2image import convert_from_path


def split_pdf_to_images(pdf_path: Path, out_dir: Path, dpi: int = 300) -> list[Path]:
    """Rasterizes each page of `pdf_path` to a PNG in `out_dir`.

    Returns the created page image paths, in page order (1-indexed filenames).
    """
    pages = convert_from_path(str(pdf_path), dpi=dpi)
    out_paths: list[Path] = []
    stem = pdf_path.stem
    for i, page in enumerate(pages, start=1):
        page_path = out_dir / f"{stem}_p{i}.png"
        page.save(page_path, "PNG")
        out_paths.append(page_path)
    return out_paths
