"""Pydantic I/O schemas.

`BoletaRecordOut` is the exact required output JSON contract from the PDR.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class BoletaRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    boleta_id: str | None = None
    date: str | None = None
    origin: str | None = None
    destination: str | None = None
    material: str | None = None
    fletero: str | None = None
    weight: float | None = None
    weight_source: Literal["measured", "estimated", "missing"] = "missing"
    trip_type: str | None = None
    tariff_amount: float | None = None
    inventory_direction: Literal["inbound", "outbound", "none", "unknown"] = "unknown"
    inventory_quantity: float | None = None
    confidence_score: float = 0.0
    status: Literal["auto_processed", "needs_review"] = "needs_review"
    exceptions: list[str] = []


class BoletaRecordDetail(BoletaRecordOut):
    """Extended view for the review UI/API: adds internal record id, audit
    hooks, and the raw OCR text so a reviewer can see what was actually read."""

    record_id: int
    boleta_id_internal: int
    ocr_text: str | None = None
    ocr_confidence: float | None = None
    matched_route_rule_id: int | None = None
    matched_tariff_rule_id: int | None = None
    matched_weight_rule_id: int | None = None
    field_confidences: dict[str, float] = {}
    image_url: str | None = None


class ReviewCorrection(BaseModel):
    """Body for POST /api/records/{id}/review."""

    action: Literal["correct", "approve"] = "correct"
    edited_by: str | None = None
    note: str | None = None

    folio: str | None = None
    date: str | None = None
    origin: str | None = None
    destination: str | None = None
    material: str | None = None
    fletero: str | None = None
    weight: float | None = None
    trip_type: str | None = None


class BatchCreate(BaseModel):
    label: str
    created_by: str | None = None
    notes: str | None = None


class BatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str
    status: str
    created_by: str | None = None
    notes: str | None = None


class BatchSummary(BaseModel):
    batch_id: int
    total_boletas: int
    auto_processed_count: int
    needs_review_count: int
    total_payment_by_fletero: dict[str, float]
    net_inventory_by_material: dict[str, float]
    net_inventory_by_route: dict[str, float]
