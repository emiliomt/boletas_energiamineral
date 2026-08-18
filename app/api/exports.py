"""Export endpoints: CSV and JSON, optionally filtered to one batch."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.exports.csv_export import build_csv_export
from app.exports.json_export import build_json_export

router = APIRouter(prefix="/api/exports", tags=["exports"])


@router.get("/csv")
def export_csv(batch_id: int | None = None, db: Session = Depends(get_db)) -> PlainTextResponse:
    content = build_csv_export(db, batch_id=batch_id)
    return PlainTextResponse(content, media_type="text/csv")


@router.get("/json")
def export_json(batch_id: int | None = None, db: Session = Depends(get_db)) -> list[dict]:
    return build_json_export(db, batch_id=batch_id)
