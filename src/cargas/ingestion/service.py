"""Ingesta: consulta cada fuente habilitada, normaliza, deduplica y marca vencimientos.

Una fuente que falla no frena a las demás: queda registrada con `last_status = 'error'` y su
mensaje, y las cargas que ya había publicado siguen en la base con su antigüedad (el ranking
las penaliza y, pasadas 48 h sin confirmación, las marca como "requiere verificación").
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from cargas.config import project_root
from cargas.ingestion.connectors import (
    Connector,
    CsvFileConnector,
    HttpJsonConnector,
    JsonFileConnector,
    SourceError,
)
from cargas.ingestion.normalize import NormalizationError, NormalizedLoad, normalize_record
from cargas.models import Load, LoadSource, LoadSourceRef

log = logging.getLogger(__name__)


@dataclass
class SourceReport:
    source: str
    status: str = "ok"
    error: str | None = None
    fetched: int = 0
    created: int = 0
    updated: int = 0
    duplicates: int = 0
    rejected: list[str] = field(default_factory=list)


@dataclass
class IngestionReport:
    sources: list[SourceReport]
    expired: int


def connector_for(source: LoadSource) -> Connector:
    cfg = source.config or {}
    if source.kind == "json":
        return JsonFileConnector(project_root() / "data" / cfg["file"])
    if source.kind == "csv":
        return CsvFileConnector(project_root() / "data" / cfg["file"])
    if source.kind == "http":
        return HttpJsonConnector(cfg["url"], timeout=float(cfg.get("timeout", 10)))
    raise SourceError(f"tipo de fuente desconocido: {source.kind}")


def _apply(load: Load, item: NormalizedLoad, seen_at: datetime) -> None:
    raw = item.raw
    load.origin_city, load.origin_province = item.origin.name, item.origin.province
    load.origin_lat, load.origin_lon = item.origin.lat, item.origin.lon
    load.dest_city, load.dest_province = item.destination.name, item.destination.province
    load.dest_lat, load.dest_lon = item.destination.lat, item.destination.lon
    load.pickup_from, load.pickup_to = raw.pickup_from, raw.pickup_to
    load.cargo_type, load.description = raw.cargo_type, raw.description
    load.pallets, load.weight_kg = raw.pallets, raw.weight_kg
    load.vehicle_types, load.body_types = raw.vehicle_types, raw.body_types
    load.requires_refrigeration = raw.requires_refrigeration
    load.price_ars, load.price_negotiable = raw.price_ars, raw.price_negotiable
    load.shipper_name, load.shipper_contact = raw.shipper_name, raw.shipper_contact
    load.status, load.valid_until = raw.status, raw.valid_until
    load.last_seen_at = seen_at
    load.raw = raw.model_dump(mode="json")


def _ingest_record(
    session: Session, source: LoadSource, item: NormalizedLoad, now: datetime, report: SourceReport
) -> None:
    seen_at = min(item.raw.published_at or now, now)
    ref = session.scalar(
        select(LoadSourceRef).where(
            LoadSourceRef.source_id == source.id,
            LoadSourceRef.external_id == item.raw.external_id,
        )
    )
    if ref is not None:
        # La misma fuente vuelve a publicar su oferta: actualización.
        load = session.get(Load, ref.load_id)
        assert load is not None
        ref.price_ars, ref.seen_at = item.raw.price_ars, seen_at
        report.updated += 1
        if load.primary_source_id != source.id:
            # Fuente secundaria de un duplicado: confirma vigencia, no pisa los datos.
            load.last_seen_at = max(seen_at, load.last_seen_at)
            return
        if load.fingerprint != item.fingerprint:
            clash = session.scalar(select(Load).where(Load.fingerprint == item.fingerprint))
            if clash is not None and clash.id != load.id:
                report.updated -= 1
                report.rejected.append(f"{item.raw.external_id}: cambió y coincide con otra carga")
                return
            load.fingerprint = item.fingerprint
        _apply(load, item, max(seen_at, load.last_seen_at))
        return

    load = session.scalar(select(Load).where(Load.fingerprint == item.fingerprint))
    if load is not None:
        # Otra fuente ya publicó la misma carga: se registra la referencia, no se duplica.
        report.duplicates += 1
        if seen_at > load.last_seen_at:
            load.last_seen_at = seen_at
    else:
        load = Load(
            fingerprint=item.fingerprint, first_seen_at=seen_at, primary_source_id=source.id
        )
        _apply(load, item, seen_at)
        session.add(load)
        session.flush()
        report.created += 1
    session.add(
        LoadSourceRef(
            load_id=load.id,
            source_id=source.id,
            external_id=item.raw.external_id,
            price_ars=item.raw.price_ars,
            seen_at=seen_at,
        )
    )


def run_source(
    session: Session, source: LoadSource, now: datetime, connector: Connector | None = None
) -> SourceReport:
    report = SourceReport(source=source.code)
    source.last_run_at = now
    try:
        records = (connector or connector_for(source)).fetch(now)
    except SourceError as exc:
        log.warning("Fuente %s con error: %s", source.code, exc)
        report.status, report.error = "error", str(exc)
        source.last_status, source.last_error = "error", str(exc)
        return report
    report.fetched = len(records)
    for record in records:
        try:
            item = normalize_record(record)
        except NormalizationError as exc:
            report.rejected.append(f"{record.get('external_id', '?')}: {exc}")
            continue
        with session.begin_nested():
            _ingest_record(session, source, item, now, report)
    source.last_status, source.last_error = "ok", None
    source.last_count = report.created + report.updated + report.duplicates
    return report


def expire_loads(session: Session, now: datetime) -> int:
    result = session.execute(
        update(Load)
        .where(Load.status == "available", Load.valid_until <= now)
        .values(status="expired")
    )
    return result.rowcount or 0


def run_ingestion(
    session: Session, now: datetime, connectors: dict[str, Connector] | None = None
) -> IngestionReport:
    connectors = connectors or {}
    sources = session.scalars(
        select(LoadSource).where(LoadSource.enabled.is_(True)).order_by(LoadSource.code)
    ).all()
    reports = [run_source(session, s, now, connectors.get(s.code)) for s in sources]
    return IngestionReport(sources=reports, expired=expire_loads(session, now))
