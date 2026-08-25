"""Loads the editable CSV config tables into the DB (idempotent upsert by
natural key), and provides cached read accessors for the rule engines.

CSV files are the source of truth an admin can edit in Excel; the DB copy
lets engines join efficiently and lets the API expose "current effective
config". Call `reload_all()` after editing a CSV to pick up changes.
"""
from __future__ import annotations

import csv
import datetime as dt
import warnings
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    ExceptionThreshold,
    PricingRule,
    Producer,
    RouteRule,
    TariffRule,
    Transportista,
    TransportistaAlias,
    WeightEstimationRule,
)
from app.parsing.normalizers import normalize_alias_text


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


def load_producers(db: Session, csv_path: Path | None = None) -> int:
    path = csv_path or settings.rules_config_dir / "producer_config.csv"
    count = 0
    for row in _read_csv(path):
        existing = db.query(Producer).filter_by(name=row["name"]).one_or_none()
        obj = existing or Producer(name=row["name"])
        obj.format_id = (row.get("format_id") or "").strip() or None
        obj.default_origin = (row.get("default_origin") or "").strip() or None
        obj.active = _to_bool(row.get("active", "true"))
        if not existing:
            db.add(obj)
        count += 1
    db.commit()
    return count


def load_transportistas(db: Session, csv_path: Path | None = None) -> int:
    """CSV shape: canonical_name,alias[,active] -- one row per alias,
    canonical repeated across its aliases' rows. alias is normalized
    (parens/phone digit runs stripped) before storage/matching. If a
    normalized alias is already claimed by a *different* canonical
    (ambiguous roster entry), the first-seen assignment wins: warn and
    leave it assigned there rather than reassigning -- keeps idempotent
    re-runs deterministic."""
    path = csv_path or settings.rules_config_dir / "transportista_roster.csv"
    count = 0
    for row in _read_csv(path):
        canonical_name = row["canonical_name"].strip()
        transportista = db.query(Transportista).filter_by(canonical_name=canonical_name).one_or_none()
        if transportista is None:
            transportista = Transportista(canonical_name=canonical_name)
            db.add(transportista)
            db.flush()  # assign transportista.id for the alias FK below

        normalized_alias = normalize_alias_text(row["alias"])
        existing_alias = db.query(TransportistaAlias).filter_by(alias_text=normalized_alias).one_or_none()
        if existing_alias is not None and existing_alias.transportista_id != transportista.id:
            other = db.get(Transportista, existing_alias.transportista_id)
            warnings.warn(
                f"transportista_roster.csv: alias '{normalized_alias}' (raw '{row['alias']}') already "
                f"claimed by '{other.canonical_name if other else existing_alias.transportista_id}'; "
                f"leaving it there, not reassigning to '{canonical_name}'.",
                stacklevel=2,
            )
            count += 1
            continue

        obj = existing_alias or TransportistaAlias(alias_text=normalized_alias)
        obj.transportista_id = transportista.id
        obj.raw_alias_text = row["alias"].strip()
        obj.active = _to_bool(row.get("active", "true"))
        if existing_alias is None:
            db.add(obj)
        count += 1
    db.commit()
    return count


def load_pricing_rules(db: Session, csv_path: Path | None = None) -> int:
    path = csv_path or settings.rules_config_dir / "pricing_config.csv"
    count = 0
    for row in _read_csv(path):
        existing = db.query(PricingRule).filter_by(rule_id=row["rule_id"]).one_or_none()
        obj = existing or PricingRule(rule_id=row["rule_id"])
        obj.scope = row["scope"].strip()
        obj.pricing_mode = row["pricing_mode"].strip()
        obj.rate = float(row["rate"])
        obj.currency = (row.get("currency") or "MXN").strip()
        obj.effective_from = row["effective_from"].strip()
        obj.effective_to = (row.get("effective_to") or "").strip() or None
        obj.active = _to_bool(row.get("active", "true"))
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
        "producers": load_producers(db),
        "transportistas": load_transportistas(db),
        "pricing_rules": load_pricing_rules(db),
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


def get_active_producers(db: Session) -> list[Producer]:
    return db.query(Producer).filter_by(active=True).all()


def get_active_transportistas(db: Session) -> list[Transportista]:
    return db.query(Transportista).filter_by(active=True).all()


def get_active_pricing_rules(db: Session, as_of: str | None = None) -> list[PricingRule]:
    """Active PricingRule rows whose effective date range covers `as_of`
    (ISO YYYY-MM-DD; defaults to today). `active` is a separate
    enable/disable flag from the date range -- a row must pass both."""
    as_of = as_of or dt.date.today().isoformat()
    rules = db.query(PricingRule).filter_by(active=True).all()
    return [
        r for r in rules
        if r.effective_from <= as_of and (r.effective_to is None or as_of <= r.effective_to)
    ]
