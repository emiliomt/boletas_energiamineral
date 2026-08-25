"""Persists uploaded files to disk and creates the DB Boleta row(s) for them.

One uploaded file becomes one or more `Boleta` rows: a single image is one
row; a multi-page PDF is split (see pdf_split.py) into one row per page, so
every downstream stage (OCR, parsing, review) always deals with one image.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.ingestion.pdf_split import split_pdf_to_images
from app.models import Batch, Boleta
from app.utils.hashing import sha256_of_bytes

IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/tiff"}
PDF_MIME_TYPE = "application/pdf"


def _batch_dir(batch_id: int) -> Path:
    d = settings.originals_dir / str(batch_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def store_upload(
    db: Session, batch: Batch, filename: str, content: bytes, mime_type: str, document_type: str = "boleta"
) -> list[Boleta]:
    """Saves `content` under data/originals/<batch_id>/, splitting PDFs into
    per-page images, and returns the created (uncommitted-processing) Boleta rows.

    `document_type` (Phase 3: "boleta" | "cfe_slip") tags which of the two
    Salida documents this upload is -- only meaningful when kind=salida,
    ignored otherwise. Manually selected by the operator at upload time
    (which file field they used), not inferred.
    """
    out_dir = _batch_dir(batch.id)
    created: list[Boleta] = []

    if mime_type == PDF_MIME_TYPE or filename.lower().endswith(".pdf"):
        pdf_path = out_dir / filename
        pdf_path.write_bytes(content)
        page_paths = split_pdf_to_images(pdf_path, out_dir)
        for page_num, page_path in enumerate(page_paths, start=1):
            boleta = Boleta(
                batch_id=batch.id,
                original_filename=filename,
                stored_path=str(page_path),
                mime_type="image/png",
                page_number=page_num,
                sha256_hash=sha256_of_bytes(page_path.read_bytes()),
                document_type=document_type,
            )
            db.add(boleta)
            created.append(boleta)
    else:
        # Disambiguate same-name uploads across a batch with a content hash prefix.
        digest = sha256_of_bytes(content)
        stored_name = f"{digest[:12]}_{filename}"
        image_path = out_dir / stored_name
        image_path.write_bytes(content)
        boleta = Boleta(
            batch_id=batch.id,
            original_filename=filename,
            stored_path=str(image_path),
            mime_type=mime_type or "image/png",
            page_number=1,
            sha256_hash=digest,
            document_type=document_type,
        )
        db.add(boleta)
        created.append(boleta)

    db.flush()  # assign ids without committing, so callers can process immediately
    return created
