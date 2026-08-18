"""Review endpoint: a reviewer submits corrected fields and/or approves a
needs_review record. See app/review/service.py for the recompute logic."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.records import _to_detail
from app.db import get_db
from app.models import BoletaRecord
from app.review.service import apply_review
from app.schemas import BoletaRecordDetail, ReviewCorrection

router = APIRouter(prefix="/api", tags=["review"])


@router.post("/records/{record_id}/review", response_model=BoletaRecordDetail)
def review_record(record_id: int, correction: ReviewCorrection, db: Session = Depends(get_db)) -> BoletaRecordDetail:
    record = db.get(BoletaRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    record = apply_review(db, record, correction)
    db.commit()
    return _to_detail(record)
