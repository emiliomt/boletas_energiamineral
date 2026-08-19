"""Server-rendered admin UI for folio batches: create a batch (sequential
or pasted-list), see status counts, and download the print-ready PDF/CSV
handed to the print vendor. Split out from routes.py to keep that file
focused on the (legacy) OCR-boleta review flow."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.db import get_db
from app.exports.folio_batch_export import build_folio_batch_csv
from app.models import Folio, FolioBatch
from app.qr.batch_pdf import generate_batch_pdf
from app.qr.generator import qr_payload_for_folio

router = APIRouter(prefix="/admin/folio-batches", tags=["web-folio-batches"])
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "web" / "templates"))


def _status_counts(db: Session, folio_batch_id: int) -> dict[str, int]:
    rows = db.query(Folio.status).filter_by(folio_batch_id=folio_batch_id).all()
    counts = {"issued": 0, "scanned": 0, "void": 0}
    for (status,) in rows:
        counts[status] = counts.get(status, 0) + 1
    return counts


@router.get("")
def list_folio_batches_web(request: Request, db: Session = Depends(get_db)):
    batches = db.query(FolioBatch).order_by(FolioBatch.id.desc()).all()
    counts_by_batch = {b.id: _status_counts(db, b.id) for b in batches}
    return templates.TemplateResponse(
        request, "folio_batches_list.html", {"batches": batches, "counts_by_batch": counts_by_batch}
    )


@router.post("")
def create_folio_batch_web(
    request: Request,
    label: str = Form(...),
    mode: str = Form(...),
    prefix: str = Form(""),
    start_number: str = Form(""),
    count: str = Form(""),
    folios_text: str = Form(""),
    vendor: str = Form(""),
    notes: str = Form(""),
    created_by: str = Form(""),
    proveedor: str = Form(""),
    destino: str = Form(""),
    contrato: str = Form(""),
    poder_calorifico_superior: str = Form(""),
    humedad_pct: str = Form(""),
    ceniza_pct: str = Form(""),
    azufre_pct: str = Form(""),
    fsi: str = Form(""),
    granulometria: str = Form(""),
    centro_explotacion: str = Form(""),
    centro_acopio: str = Form(""),
    concesion_minera: str = Form(""),
    representante_legal: str = Form(""),
    db: Session = Depends(get_db),
):
    def _error(message: str):
        batches = db.query(FolioBatch).order_by(FolioBatch.id.desc()).all()
        counts_by_batch = {b.id: _status_counts(db, b.id) for b in batches}
        return templates.TemplateResponse(
            request,
            "folio_batches_list.html",
            {"batches": batches, "counts_by_batch": counts_by_batch, "error": message},
        )

    if mode == "sequential":
        folio_values = [f"{prefix}{n}" for n in range(int(start_number), int(start_number) + int(count))]
    else:
        seen: set[str] = set()
        folio_values = []
        for line in folios_text.splitlines():
            f = line.strip()
            if not f:
                continue
            if f in seen:
                return _error(f"Folio repetido en la lista pegada: {f}")
            seen.add(f)
            folio_values.append(f)
        if not folio_values:
            return _error("La lista de folios está vacía.")

    existing = db.query(Folio.folio).filter(Folio.folio.in_(folio_values)).all()
    if existing:
        collided = ", ".join(f for (f,) in existing[:10])
        return _error(f"Folio(s) ya existen: {collided}")

    batch = FolioBatch(
        label=label,
        mode=mode,
        prefix=prefix or None,
        start_number=int(start_number) if mode == "sequential" and start_number else None,
        count=len(folio_values),
        vendor=vendor or None,
        notes=notes or None,
        created_by=created_by or None,
        proveedor=proveedor or None,
        destino=destino or None,
        contrato=contrato or None,
        poder_calorifico_superior=poder_calorifico_superior or None,
        humedad_pct=humedad_pct or None,
        ceniza_pct=ceniza_pct or None,
        azufre_pct=azufre_pct or None,
        fsi=fsi or None,
        granulometria=granulometria or None,
        centro_explotacion=centro_explotacion or None,
        centro_acopio=centro_acopio or None,
        concesion_minera=concesion_minera or None,
        representante_legal=representante_legal or None,
    )
    db.add(batch)
    db.flush()
    for folio_value in folio_values:
        db.add(Folio(folio_batch_id=batch.id, folio=folio_value, qr_payload=qr_payload_for_folio(folio_value)))
    db.commit()
    return RedirectResponse(url=f"/admin/folio-batches/{batch.id}", status_code=303)


@router.get("/{folio_batch_id}")
def folio_batch_detail_web(request: Request, folio_batch_id: int, db: Session = Depends(get_db)):
    batch = db.get(FolioBatch, folio_batch_id)
    counts = _status_counts(db, folio_batch_id)
    folios = db.query(Folio).filter_by(folio_batch_id=folio_batch_id).order_by(Folio.id).all()
    return templates.TemplateResponse(
        request, "folio_batch_detail.html", {"batch": batch, "counts": counts, "folios": folios}
    )


@router.get("/{folio_batch_id}/print-pdf")
def download_print_pdf_web(folio_batch_id: int, db: Session = Depends(get_db)) -> Response:
    batch = db.get(FolioBatch, folio_batch_id)
    folios = db.query(Folio).filter_by(folio_batch_id=folio_batch_id).order_by(Folio.id).all()
    pdf_bytes = generate_batch_pdf(batch, folios)
    filename = f"boletas_{batch.label.replace(' ', '_')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{folio_batch_id}/export-csv")
def download_export_csv_web(folio_batch_id: int, db: Session = Depends(get_db)) -> PlainTextResponse:
    folios = db.query(Folio).filter_by(folio_batch_id=folio_batch_id).order_by(Folio.id).all()
    return PlainTextResponse(build_folio_batch_csv(folios), media_type="text/csv")
