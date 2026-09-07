#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict

from app.config import settings  # noqa: F401 (import ensures settings are loaded)
from app.ocr.factory import get_ocr_adapter
from app.parsing.field_parser import ParsedFields, parse_fields


def dataclass_to_dict(obj: Any) -> Any:
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, (list, tuple)):
        return [dataclass_to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: dataclass_to_dict(v) for k, v in obj.items()}
    return obj


def run_for_backend(image_path: Path, backend: str) -> Dict[str, Any]:
    # Drive the app’s factory by setting the selected backend on the live settings
    # instance. This mirrors OCR_BACKEND without needing a process restart.
    from app.config import settings as live_settings

    live_settings.ocr_backend = backend
    adapter = get_ocr_adapter()
    ocr = adapter.extract(image_path)
    parsed: ParsedFields = parse_fields(ocr)
    return {
        "backend": backend,
        "ocr_engine": ocr.engine,
        "ocr_confidence": round(float(ocr.confidence), 3),
        "parsed": dataclass_to_dict(parsed),
        "raw_text": ocr.text,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run boleta OCR (tesseract/openai) and parse fields.")
    parser.add_argument("image", type=Path, help="Path to boleta image (jpg/png/webp)")
    parser.add_argument(
        "--backends",
        default="tesseract,openai",
        help="Comma-separated list of backends to run: tesseract,openai (default: both)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("/opt/cursor/artifacts"),
        help="Directory to write artifacts (JSON + raw text). Default: /opt/cursor/artifacts",
    )
    args = parser.parse_args()

    image_path: Path = args.image
    if not image_path.exists():
        raise SystemExit(f"Image not found: {image_path}")

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    backends = [b.strip().lower() for b in args.backends.split(",") if b.strip()]
    results: Dict[str, Any] = {"image": str(image_path), "runs": {}}

    for backend in backends:
        result = run_for_backend(image_path, backend)
        results["runs"][backend] = result
        # Write per-backend raw text for quick inspection
        raw_txt_path = out_dir / f"{image_path.stem}.{backend}.txt"
        raw_txt_path.write_text(result["raw_text"])

    # Write combined JSON
    out_json = out_dir / f"{image_path.stem}.ocr_compare.json"
    out_json.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(out_json)


if __name__ == "__main__":
    # Ensure BASE_DIR/data exists for any codepaths that expect it
    os.makedirs("data", exist_ok=True)
    main()

