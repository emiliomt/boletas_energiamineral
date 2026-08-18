#!/usr/bin/env python3
"""Runs the full pipeline (real Tesseract OCR + rule engines) over every
image/PDF in a folder, using a fresh scratch DB seeded from the sample
config CSVs, and writes each record's output JSON plus a CSV summary.

Usage:
  python scripts/run_pipeline_on_folder.py --input tests/fixtures/boletas --output /tmp/boletas_run
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db import Base  # noqa: E402
from app.exports.csv_export import build_csv_export  # noqa: E402
from app.exports.json_export import build_json_export  # noqa: E402
from app.ingestion.storage import PDF_MIME_TYPE, store_upload  # noqa: E402
from app.models import Batch  # noqa: E402
from app.ocr.tesseract_adapter import TesseractOCRAdapter  # noqa: E402
from app.pipeline.orchestrator import process_boleta  # noqa: E402
from app.rules.config_loader import reload_all  # noqa: E402

EXT_TO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".pdf": PDF_MIME_TYPE,
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Folder of boleta images/PDFs to process")
    parser.add_argument("--output", required=True, help="Folder to write scratch DB, JSON, and CSV output into")
    args = parser.parse_args()

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Fresh scratch DB per run — never touches the app's real data/boletas.db.
    db_path = output_dir / "pipeline_run.db"
    db_path.unlink(missing_ok=True)
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = session_local()
    reload_all(db)

    # Uploaded originals for this run go alongside the scratch DB, not into data/originals.
    import app.config as app_config

    app_config.settings.originals_dir = output_dir / "originals"

    batch = Batch(label=f"CLI run: {input_dir.name}")
    db.add(batch)
    db.flush()

    adapter = TesseractOCRAdapter()
    files = sorted(p for p in input_dir.iterdir() if p.suffix.lower() in EXT_TO_MIME)
    if not files:
        print(f"No image/PDF files found in {input_dir}")
        return

    for path in files:
        content = path.read_bytes()
        mime_type = EXT_TO_MIME[path.suffix.lower()]
        boletas = store_upload(db, batch, path.name, content, mime_type)
        for boleta in boletas:
            record = process_boleta(db, boleta, adapter)
            print(f"\n=== {path.name} (page {boleta.page_number}) -> record #{record.id} ===")
            print(json.dumps(build_json_export(db, batch_id=batch.id)[-1], indent=2, ensure_ascii=False))
    db.commit()

    csv_path = output_dir / "results.csv"
    csv_path.write_text(build_csv_export(db, batch_id=batch.id), encoding="utf-8")

    json_path = output_dir / "results.json"
    json_path.write_text(
        json.dumps(build_json_export(db, batch_id=batch.id), indent=2, ensure_ascii=False), encoding="utf-8"
    )

    all_records = build_json_export(db, batch_id=batch.id)
    auto = sum(1 for r in all_records if r["status"] == "auto_processed")
    print(f"\n{len(all_records)} boletas processed: {auto} auto_processed, {len(all_records) - auto} needs_review")
    print(f"CSV written to {csv_path}")
    print(f"JSON written to {json_path}")


if __name__ == "__main__":
    main()
