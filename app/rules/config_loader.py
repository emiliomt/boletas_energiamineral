"""Loads the editable CSV config tables into the DB (idempotent upsert by
natural key), and provides cached read accessors for the rule engines.

CSV files are the source of truth an admin can edit in Excel; the DB copy
lets engines join efficiently and lets the API expose "current effective
config". Call `reload_all()` after editing a CSV to pick up changes.
"""
from __future__ import annotations

import csv
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models import ExceptionThreshold, RouteRule, TariffRule, WeightEstimationRule


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        # '#'-prefixed lines are comments (used for the placeholder-data notice)
        rows = [line for line in f if not line.lstrip().startswith("#")]
    return list(csv.DictReader(rows))


def _to_bool(value: str | None) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes")


def load_routes(db: Session, csv_path: Path | None = None) -> int:
    path = csv_path or settings.rules_config_dir / "route_config.csv"
    count = 0
    for row in _read_csv(path):
        existing = db.query(RouteRule).filter_by(route_id=row["route_id"]).one_or_none()
        obj = existing or RouteRule(route_id=row["route_id"])
        obj.origin = row["origin"].strip()
        obj.destination = row["destination"].strip()
        obj.trip_type = row["trip_type"].strip()
        obj.inventory_direction = row["inventory_direction"].strip()
        obj.material = (row.get("material") or "").strip() or None
        obj.distance_band = (row.get("distance_band") or "").strip() or None
        obj.active = _to_bool(row.get("active", "true"))
        if not existing:
            db.add(obj)
        count += 1
    db.commit()
    return count


def load_tariffs(db: Session, csv_path: Path | None = None) -> int:
    path = csv_path or settings.rules_config_dir / "tariff_config.csv"
    count = 0
    for row in _read_csv(path):
        existing = db.query(TariffRule).filter_by(tariff_id=row["tariff_id"]).one_or_none()
        obj = existing or TariffRule(tariff_id=row["tariff_id"])
        obj.trip_type = row["trip_type"].strip()
        obj.distance_band = row["distance_band"].strip()
        obj.tariff_amount = float(row["tariff_amount"])
        obj.currency = (row.get("currency") or "MXN").strip()
        obj.active = _to_bool(row.get("active", "true"))
        if not existing:
            db.add(obj)
        count += 1
    db.commit()
    return count


def load_weight_rules(db: Session, csv_path: Path | None = None) -> int:
    path = csv_path or settings.rules_config_dir / "weight_estimation_config.csv"
    count = 0
    for row in _read_csv(path):
        existing = db.query(WeightEstimationRule).filter_by(rule_id=row["rule_id"]).one_or_none()
        obj = existing or WeightEstimationRule(rule_id=row["rule_id"])
        obj.trip_type = row["trip_type"].strip()
        obj.material = (row.get("material") or "").strip() or None
        obj.estimated_weight_kg = float(row["estimated_weight_kg"])
        obj.conditions = (row.get("conditions") or "").strip() or None
        obj.active = _to_bool(row.get("active", "true"))
        if not existing:
            db.add(obj)
        count += 1
    db.commit()
    return count


def load_exception_thresholds(db: Session, csv_path: Path | None = None) -> int:
    path = csv_path or settings.rules_config_dir / "exception_thresholds.csv"
    count = 0
    for row in _read_csv(path):
        existing = (
            db.query(ExceptionThreshold).filter_by(condition_key=row["condition_key"]).one_or_none()
        )
        obj = existing or ExceptionThreshold(condition_key=row["condition_key"])
        obj.threshold_value = (row.get("threshold_value") or "").strip() or None
        obj.behavior = row["behavior"].strip()
        obj.description = (row.get("description") or "").strip() or None
        obj.active = True
        if not existing:
            db.add(obj)
        count += 1
    db.commit()
    return count


def reload_all(db: Session) -> dict[str, int]:
    """Re-import every config CSV. Returns a per-table row count for confirmation."""
    return {
        "routes": load_routes(db),
        "tariffs": load_tariffs(db),
        "weight_rules": load_weight_rules(db),
        "exception_thresholds": load_exception_thresholds(db),
    }


# --- Cached read accessors used by the rule engines -------------------------


def get_active_routes(db: Session) -> list[RouteRule]:
    return db.query(RouteRule).filter_by(active=True).all()


def get_active_tariffs(db: Session) -> list[TariffRule]:
    return db.query(TariffRule).filter_by(active=True).all()


def get_active_weight_rules(db: Session) -> list[WeightEstimationRule]:
    return db.query(WeightEstimationRule).filter_by(active=True).all()


def get_thresholds(db: Session) -> dict[str, ExceptionThreshold]:
    return {t.condition_key: t for t in db.query(ExceptionThreshold).filter_by(active=True).all()}
