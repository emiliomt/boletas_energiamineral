"""Pydantic I/O schemas.

`BoletaRecordOut` is the exact required output JSON contract from the PDR.
"""
from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


class BoletaRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    boleta_id: str | None = None
    date: str | None = None
    origin: str | None = None
    destination: str | None = None
    material: str | None = None
    fletero: str | None = None
    weight: float | None = None
    weight_declared: float | None = None  # Volumen por Entregar (initial/planned); weight itself is Volumen Entregado (actual)
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
    kind: Literal["entrada", "salida"] = "salida"
    producer_id: int | None = None
    ocr_text: str | None = None
    ocr_confidence: float | None = None
    ocr_engine: str | None = None
    secondary_origin: str | None = None  # Centro de Acopio
    contract_number: str | None = None
    truck_box_number: str | None = None  # No. Caja
    proveedor: str | None = None
    concesion_minera: str | None = None  # Datos de Concesión Minera
    representante_legal: str | None = None  # Nombre (Representante Legal)
    quality_data: dict[str, str] = {}
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

    # Remaining boleta fields the reviewer can also correct.
    secondary_origin: str | None = None  # Centro de Acopio
    contract_number: str | None = None  # Contrato
    truck_box_number: str | None = None  # No. Caja
    weight_declared: float | None = None  # Volumen por Entregar
    proveedor: str | None = None
    concesion_minera: str | None = None  # Datos de Concesión Minera
    representante_legal: str | None = None  # Nombre (Representante Legal)
    # Coal-quality metrics (stored in BoletaRecord.quality_data).
    poder_calorifico_superior: str | None = None
    humedad_pct: str | None = None
    ceniza_pct: str | None = None
    azufre_pct: str | None = None
    fsi: str | None = None
    granulometria: str | None = None


class BatchCreate(BaseModel):
    label: str
    created_by: str | None = None
    notes: str | None = None
    kind: Literal["entrada", "salida"] = "salida"
    producer_id: int | None = None  # required when kind="entrada"

    @model_validator(mode="after")
    def _check_producer_required_for_entrada(self) -> "BatchCreate":
        if self.kind == "entrada" and self.producer_id is None:
            raise ValueError("producer_id is required when kind='entrada'")
        return self


class BatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str
    status: str
    created_by: str | None = None
    notes: str | None = None
    kind: Literal["entrada", "salida"] = "salida"
    producer_id: int | None = None


class BatchSummary(BaseModel):
    batch_id: int
    total_boletas: int
    auto_processed_count: int
    needs_review_count: int
    total_payment_by_fletero: dict[str, float]
    net_inventory_by_material: dict[str, float]
    net_inventory_by_route: dict[str, float]


class FolioBatchCreate(BaseModel):
    """Body for POST /api/folio-batches. `mode="sequential"` generates
    prefix+start_number..prefix+start_number+count-1; `mode="imported"`
    uses the explicit `folios` list (e.g. pasted from the client's own
    existing numbering)."""

    label: str
    mode: Literal["sequential", "imported"]
    prefix: str | None = None
    start_number: int | None = None
    count: int | None = None
    folios: list[str] | None = None
    vendor: str | None = None
    notes: str | None = None
    created_by: str | None = None

    # Batch-level boleta data entered online, pre-printed on every folio.
    proveedor: str | None = None
    destino: str | None = None
    contrato: str | None = None
    poder_calorifico_superior: str | None = None
    humedad_pct: str | None = None
    ceniza_pct: str | None = None
    azufre_pct: str | None = None
    fsi: str | None = None
    granulometria: str | None = None
    centro_explotacion: str | None = None
    centro_acopio: str | None = None
    concesion_minera: str | None = None
    representante_legal: str | None = None

    @model_validator(mode="after")
    def _check_mode_fields(self) -> "FolioBatchCreate":
        if self.mode == "sequential":
            if self.prefix is None or self.start_number is None or not self.count:
                raise ValueError("sequential mode requires prefix, start_number, and count")
        else:
            if not self.folios:
                raise ValueError("imported mode requires a non-empty folios list")
        return self


class FolioBatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str
    mode: str
    count: int
    vendor: str | None = None
    notes: str | None = None
    created_by: str | None = None
    created_at: dt.datetime

    proveedor: str | None = None
    destino: str | None = None
    contrato: str | None = None
    poder_calorifico_superior: str | None = None
    humedad_pct: str | None = None
    ceniza_pct: str | None = None
    azufre_pct: str | None = None
    fsi: str | None = None
    granulometria: str | None = None
    centro_explotacion: str | None = None
    centro_acopio: str | None = None
    concesion_minera: str | None = None
    representante_legal: str | None = None


class FolioBatchDetail(FolioBatchOut):
    issued_count: int
    scanned_count: int
    void_count: int
