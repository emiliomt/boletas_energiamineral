"""Export endpoints: CSV, JSON, and the per-batch Excel payment-confirmation
workbook."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.exports.csv_export import build_csv_export
from app.exports.json_export import build_json_export
from app.exports.xlsx_export import build_batch_xlsx

router = APIRouter(prefix="/api/exports", tags=["exports"])


@router.get("/csv")
def export_csv(batch_id: int | None = None, db: Session = Depends(get_db)) -> PlainTextResponse:
    content = build_csv_export(db, batch_id=batch_id)
    return PlainTextResponse(content, media_type="text/csv")


@router.get("/json")
def export_json(batch_id: int | None = None, db: Session = Depends(get_db)) -> list[dict]:
    return build_json_export(db, batch_id=batch_id)


@router.get("/xlsx")
def export_xlsx(batch_id: int, db: Session = Depends(get_db)) -> Response:
    content = build_batch_xlsx(db, batch_id=batch_id)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="boletas_lote_{batch_id}.xlsx"'},
    )
