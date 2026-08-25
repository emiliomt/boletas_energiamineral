"""SQLAlchemy ORM models.

Two tables represent each processed document on purpose: `Boleta` is the
immutable record of what was uploaded (so the source scan and its audit
trail survive even if the derived record is corrected or reprocessed), and
`BoletaRecord` is the derived/parsed/computed record a reviewer edits.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Batch(Base):
    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="open")  # open | closed
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    boletas: Mapped[list["Boleta"]] = relationship(back_populates="batch")


class Boleta(Base):
    """The raw uploaded artifact (one row per page/image)."""

    __tablename__ = "boletas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"))
    original_filename: Mapped[str] = mapped_column(String(512))
    stored_path: Mapped[str] = mapped_column(String(1024))
    mime_type: Mapped[str] = mapped_column(String(128))
    page_number: Mapped[int] = mapped_column(Integer, default=1)
    sha256_hash: Mapped[str] = mapped_column(String(64), index=True)
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

    batch: Mapped["Batch"] = relationship(back_populates="boletas")
    record: Mapped["BoletaRecord"] = relationship(
        back_populates="boleta", uselist=False, cascade="all, delete-orphan"
    )


class BoletaRecord(Base):
    """The parsed/classified/computed output for one Boleta.

    Field names intentionally mirror the required output JSON schema
    (see app/schemas.py) so serialization is a near-direct mapping.
    """

    __tablename__ = "boleta_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    boleta_id: Mapped[int] = mapped_column(ForeignKey("boletas.id"), unique=True)

    kind: Mapped[str] = mapped_column(String(16), default="salida")  # entrada|salida
    producer_id: Mapped[int | None] = mapped_column(
        ForeignKey("producers.id"), nullable=True
    )  # set for kind=entrada, Phase 2+

    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    ocr_engine: Mapped[str | None] = mapped_column(String(64), nullable=True)  # e.g. "tesseract" or "openai:gpt-4o-mini"

    folio: Mapped[str | None] = mapped_column(String(128), nullable=True)
    date: Mapped[str | None] = mapped_column(String(32), nullable=True)  # ISO YYYY-MM-DD
    origin: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Centro de Explotación
    secondary_origin: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Centro de Acopio (captured, not classification-relevant)
    destination: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contract_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    material: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fletero: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Datos del chofer del camión
    truck_box_number: Mapped[str | None] = mapped_column(String(64), nullable=True)  # No. Caja
    proveedor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    concesion_minera: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Datos de Concesión Minera
    representante_legal: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Nombre (Representante Legal)

    weight: Mapped[float | None] = mapped_column(Float, nullable=True)  # Volumen Entregado (actual) -- drives tariff/inventory
    weight_declared: Mapped[float | None] = mapped_column(Float, nullable=True)  # Volumen por Entregar (initial/planned)
    weight_source: Mapped[str] = mapped_column(String(16), default="missing")  # measured|estimated|missing
    quality_data: Mapped[dict] = mapped_column(JSON, default=dict)  # coal quality metrics, provenance only -- not used in tariff/inventory math

    trip_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tariff_amount: Mapped[float | None] = mapped_column(Float, nullable=True)

    inventory_direction: Mapped[str] = mapped_column(String(16), default="unknown")  # inbound|outbound|none|unknown
    inventory_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)

    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="needs_review")  # auto_processed|needs_review
    exceptions: Mapped[list] = mapped_column(JSON, default=list)
    field_confidences: Mapped[dict] = mapped_column(JSON, default=dict)

    matched_route_rule_id: Mapped[int | None] = mapped_column(ForeignKey("route_rules.id"), nullable=True)
    matched_tariff_rule_id: Mapped[int | None] = mapped_column(ForeignKey("tariff_rules.id"), nullable=True)
    matched_weight_rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("weight_estimation_rules.id"), nullable=True
    )

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)

    boleta: Mapped["Boleta"] = relationship(back_populates="record")
    audits: Mapped[list["ReviewAudit"]] = relationship(back_populates="record", cascade="all, delete-orphan")


class ReviewAudit(Base):
    """One row per human correction/approval action, for full traceability."""

    __tablename__ = "review_audits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    boleta_record_id: Mapped[int] = mapped_column(ForeignKey("boleta_records.id"))
    field_name: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(String(32))  # correction | approval
    edited_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    edited_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    record: Mapped["BoletaRecord"] = relationship(back_populates="audits")


class RouteRule(Base):
    """Config table: which origin/destination pairs mean what trip."""

    __tablename__ = "route_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    route_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    origin: Mapped[str] = mapped_column(String(255))
    destination: Mapped[str] = mapped_column(String(255))
    trip_type: Mapped[str] = mapped_column(String(128))
    inventory_direction: Mapped[str] = mapped_column(String(16))  # inbound|outbound|none
    material: Mapped[str | None] = mapped_column(String(255), nullable=True)
    distance_band: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class TariffRule(Base):
    """Config table: what a trip_type/distance_band pays."""

    __tablename__ = "tariff_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tariff_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    trip_type: Mapped[str] = mapped_column(String(128))
    distance_band: Mapped[str] = mapped_column(String(64))
    tariff_amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="MXN")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class WeightEstimationRule(Base):
    """Config table: estimated weight to apply when the scan has none."""

    __tablename__ = "weight_estimation_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    trip_type: Mapped[str] = mapped_column(String(128))
    material: Mapped[str | None] = mapped_column(String(255), nullable=True)
    estimated_weight_kg: Mapped[float] = mapped_column(Float)
    conditions: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Producer(Base):
    """Config table: an Entrada producer/contract (e.g. "Bradfort",
    "CTU/MINSA"). format_id feeds per-producer template matching (Phase 4);
    default_origin feeds Entrada classification (Phase 2). Both inert in
    Phase 1."""

    __tablename__ = "producers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)  # natural key
    format_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    default_origin: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Transportista(Base):
    """Config table: a canonical transportista identity. Aliases
    (handwriting/spelling variants) live in TransportistaAlias."""

    __tablename__ = "transportistas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(255), unique=True, index=True)  # natural key
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    aliases: Mapped[list["TransportistaAlias"]] = relationship(
        back_populates="transportista", cascade="all, delete-orphan"
    )


class TransportistaAlias(Base):
    """One normalized alias string for a Transportista, loaded from
    transportista_roster.csv (one row per alias). alias_text is stored
    already normalized (parens/phone-number digit runs stripped — see
    app/parsing/normalizers.normalize_alias_text) so lookups compare like
    for like; raw_alias_text keeps the original for audit."""

    __tablename__ = "transportista_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transportista_id: Mapped[int] = mapped_column(ForeignKey("transportistas.id"))
    alias_text: Mapped[str] = mapped_column(String(255), unique=True, index=True)  # normalized, natural key
    raw_alias_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    transportista: Mapped["Transportista"] = relationship(back_populates="aliases")


class PricingRule(Base):
    """Config table: a per-trip pricing rate (flat or weight-multiplied)
    with an effective date range. Deliberately separate from TariffRule
    (Salida-era, keyed by trip_type/distance_band) -- see PRD Phase 1 §5.5.
    `scope` is a plain string (origin name or Producer.name, interpretation
    depends on `kind`) -- whether it becomes a strict FK for Entradas is an
    open question for Phase 2/3 (PRD §11)."""

    __tablename__ = "pricing_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # natural key
    scope: Mapped[str] = mapped_column(String(255), index=True)
    pricing_mode: Mapped[str] = mapped_column(String(16))  # flat|per_weight
    rate: Mapped[float] = mapped_column(Float)  # flat MXN amount, or MXN per ton for per_weight
    currency: Mapped[str] = mapped_column(String(8), default="MXN")  # added for TariffRule-consistency
    effective_from: Mapped[str] = mapped_column(String(32))  # ISO YYYY-MM-DD, matches BoletaRecord.date convention
    effective_to: Mapped[str | None] = mapped_column(String(32), nullable=True)  # null = open-ended
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class FolioBatch(Base):
    """A batch of folios (+ QR codes) we generated and handed to the print
    vendor. Distinct from `Batch` above, which is a batch of *scanned
    uploads* — this is a batch of *pre-issued, not-yet-scanned* folios."""

    __tablename__ = "folio_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(255))
    mode: Mapped[str] = mapped_column(String(16))  # sequential | imported
    prefix: Mapped[str | None] = mapped_column(String(32), nullable=True)  # sequential mode
    start_number: Mapped[int | None] = mapped_column(Integer, nullable=True)  # sequential mode
    count: Mapped[int] = mapped_column(Integer)
    vendor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

    # Batch-level boleta data entered ONLINE before printing. These are
    # constant for every boleta in the lote (same contract/route/quality
    # spec/origin), so they get pre-printed onto each generated boleta page
    # (see app/qr/batch_pdf.py). The remaining per-trip fields (fecha, chofer,
    # no. caja, volúmenes, firma) are left blank to be filled by hand at the
    # delivery point and OCR'd back when the boleta is scanned.
    proveedor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    destino: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contrato: Mapped[str | None] = mapped_column(String(64), nullable=True)
    poder_calorifico_superior: Mapped[str | None] = mapped_column(String(64), nullable=True)
    humedad_pct: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ceniza_pct: Mapped[str | None] = mapped_column(String(64), nullable=True)
    azufre_pct: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fsi: Mapped[str | None] = mapped_column(String(64), nullable=True)
    granulometria: Mapped[str | None] = mapped_column(String(64), nullable=True)
    centro_explotacion: Mapped[str | None] = mapped_column(String(255), nullable=True)
    centro_acopio: Mapped[str | None] = mapped_column(String(255), nullable=True)
    concesion_minera: Mapped[str | None] = mapped_column(String(255), nullable=True)
    representante_legal: Mapped[str | None] = mapped_column(String(255), nullable=True)

    folios: Mapped[list["Folio"]] = relationship(back_populates="folio_batch", cascade="all, delete-orphan")


class Folio(Base):
    """One pre-issued folio (+ QR payload) within a FolioBatch. Scanning a
    boleta whose folio isn't `issued` here (or is already `scanned`) is
    flagged — see app/engines/folio_registry.py."""

    __tablename__ = "folios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    folio_batch_id: Mapped[int] = mapped_column(ForeignKey("folio_batches.id"))
    folio: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    qr_payload: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(16), default="issued")  # issued | scanned | void
    issued_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    scanned_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    boleta_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("boleta_records.id"), nullable=True, unique=True
    )

    folio_batch: Mapped["FolioBatch"] = relationship(back_populates="folios")


class ExceptionThreshold(Base):
    """Config table: tunable thresholds/behaviors driving confidence & status."""

    __tablename__ = "exception_thresholds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    condition_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    threshold_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    behavior: Mapped[str] = mapped_column(String(32))  # hard_block|soft_flag|auto_process_if_no_exceptions|config
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
