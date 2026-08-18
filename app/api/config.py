"""Config read/reload endpoints: expose the current effective rule tables
and let an admin re-import the CSVs after an edit."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ExceptionThreshold, RouteRule, TariffRule, WeightEstimationRule
from app.rules.config_loader import reload_all

router = APIRouter(prefix="/api/config", tags=["config"])


def _as_dict(obj) -> dict:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


@router.get("/routes")
def get_routes(db: Session = Depends(get_db)) -> list[dict]:
    return [_as_dict(r) for r in db.query(RouteRule).order_by(RouteRule.route_id).all()]


@router.get("/tariffs")
def get_tariffs(db: Session = Depends(get_db)) -> list[dict]:
    return [_as_dict(t) for t in db.query(TariffRule).order_by(TariffRule.tariff_id).all()]


@router.get("/weights")
def get_weights(db: Session = Depends(get_db)) -> list[dict]:
    return [_as_dict(w) for w in db.query(WeightEstimationRule).order_by(WeightEstimationRule.rule_id).all()]


@router.get("/thresholds")
def get_thresholds_config(db: Session = Depends(get_db)) -> list[dict]:
    return [_as_dict(t) for t in db.query(ExceptionThreshold).order_by(ExceptionThreshold.condition_key).all()]


@router.post("/reload")
def reload_config(db: Session = Depends(get_db)) -> dict:
    counts = reload_all(db)
    return {"reloaded": counts}
