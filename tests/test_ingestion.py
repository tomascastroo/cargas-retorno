"""Ingesta sobre la base de test (las fuentes demo ya se cargaron en conftest)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from cargas.db import public_session
from cargas.ingestion.connectors import SourceError
from cargas.ingestion.normalize import NormalizationError, normalize_record
from cargas.ingestion.service import run_ingestion, run_source
from cargas.models import Load, LoadSource, LoadSourceRef


class StaticConnector:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records

    def fetch(self, now: datetime) -> list[dict[str, Any]]:
        return self.records


class BrokenConnector:
    def fetch(self, now: datetime) -> list[dict[str, Any]]:
        raise SourceError("timeout consultando la fuente")


def record(now: datetime, **kw: Any) -> dict[str, Any]:
    base = {
        "external_id": f"T-{uuid.uuid4().hex[:6]}",
        "origin": "Rosario",
        "destination": "Córdoba",
        "pickup_from": (now + timedelta(days=1)).date().isoformat(),
        "cargo_type": "general",
        "description": "Carga de prueba",
        "pallets": 10,
        "price_ars": "1.000.000",
        "shipper_name": f"Dador {uuid.uuid4().hex[:6]} S.A.",
        "valid_until": (now + timedelta(days=2)).isoformat(),
    }
    base.update(kw)
    return base


def make_source(session, code: str | None = None, reliability: float = 0.6) -> LoadSource:
    source = LoadSource(
        code=code or f"test-{uuid.uuid4().hex[:6]}",
        name="Fuente de test",
        kind="json",
        reliability=reliability,
        config={},
    )
    session.add(source)
    session.flush()
    return source


def test_demo_sources_loaded_with_duplicate_and_rejected_record() -> None:
    with public_session() as session:
        sources = {s.code: s for s in session.scalars(select(LoadSource))}
        assert sources["dadores-demo"].last_status == "ok"
        assert sources["planilla-cooperativa-demo"].last_status == "ok"
        assert sources["socio-api-ejemplo"].last_run_at is None  # deshabilitada
        # D01 (JSON) y COOP-118 (CSV) son la misma oferta escrita distinto: una sola carga.
        harina = session.scalars(select(Load).where(Load.description.ilike("%harina%"))).all()
        assert len(harina) == 1
        refs = session.scalars(select(LoadSourceRef).where(LoadSourceRef.load_id == harina[0].id))
        assert {r.external_id for r in refs} == {"D01", "COOP-118"}
        # La oferta vencida queda marcada.
        expired = session.scalar(select(Load).where(Load.description.ilike("%vencida%")))
        assert expired is not None and expired.status == "expired"


def test_reingesting_same_source_updates_instead_of_duplicating() -> None:
    now = datetime.now(UTC)
    rec = record(now)
    with public_session() as session:
        source = make_source(session)
        first = run_source(session, source, now, StaticConnector([rec]))
        second = run_source(
            session,
            source,
            now + timedelta(hours=1),
            StaticConnector([{**rec, "price_ars": 1_100_000}]),
        )
        assert (first.created, second.created, second.updated) == (1, 0, 1)
        load = session.scalar(
            select(Load).join(LoadSourceRef).where(LoadSourceRef.source_id == source.id)
        )
        assert load.price_ars == 1_100_000


def test_same_offer_from_two_sources_is_detected_as_duplicate() -> None:
    now = datetime.now(UTC)
    rec = record(now, shipper_name="Acopio Pampa S.A.", origin="ROSARIO")
    with public_session() as session:
        a, b = make_source(session), make_source(session)
        run_source(session, a, now, StaticConnector([rec]))
        report = run_source(
            session,
            b,
            now,
            StaticConnector(
                [
                    {
                        **rec,
                        "external_id": "otro-id",
                        "shipper_name": "ACOPIO PAMPA SA",
                        "origin": "rosario",
                        "price_ars": 950000,
                    }
                ]
            ),
        )
        assert report.duplicates == 1 and report.created == 0
        loads = session.scalar(
            select(func.count())
            .select_from(LoadSourceRef)
            .where(LoadSourceRef.source_id.in_([a.id, b.id]))
        )
        assert loads == 2  # dos referencias, una sola carga


def test_failing_source_does_not_stop_the_others() -> None:
    now = datetime.now(UTC)
    with public_session() as session:
        broken, ok = make_source(session, "zz-broken"), make_source(session, "zz-ok")
        # Sólo estas dos fuentes: se deshabilitan temporalmente las demás.
        others = session.scalars(
            select(LoadSource).where(
                LoadSource.id.not_in([broken.id, ok.id]), LoadSource.enabled.is_(True)
            )
        ).all()
        for s in others:
            s.enabled = False
        report = run_ingestion(
            session, now, {"zz-broken": BrokenConnector(), "zz-ok": StaticConnector([record(now)])}
        )
        by_code = {r.source: r for r in report.sources}
        assert by_code["zz-broken"].status == "error"
        assert "timeout" in (broken.last_error or "")
        assert by_code["zz-ok"].created == 1
        session.rollback()  # no dejar las fuentes demo deshabilitadas


def test_expire_marks_past_offers() -> None:
    now = datetime.now(UTC)
    with public_session() as session:
        source = make_source(session)
        others = session.scalars(select(LoadSource).where(LoadSource.id != source.id)).all()
        for s in others:
            s.enabled = False
        report = run_ingestion(
            session,
            now,
            {
                source.code: StaticConnector(
                    [record(now, valid_until=(now - timedelta(minutes=1)).isoformat())]
                )
            },
        )
        assert report.expired >= 1
        session.rollback()


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"origin": "Pueblo Inventado"}, "localidad desconocida"),
        ({"price_ars": -5}, "price_ars"),
        ({"pallets": 200}, "pallets"),
        ({"valid_until": "2026-10-20T10:00:00"}, "zona horaria"),
        ({"pickup_to": "2020-01-01"}, "pickup_to"),
    ],
)
def test_invalid_records_are_rejected(override: dict[str, Any], message: str) -> None:
    with pytest.raises(NormalizationError, match=message):
        normalize_record(record(datetime.now(UTC), **override))


def test_secondary_source_reingest_does_not_overwrite_primary_data() -> None:
    now = datetime.now(UTC)
    rec = record(now, shipper_name="Cerealera Norte S.A.", description="Datos de la primaria")
    dup = {**rec, "external_id": "sec-1", "description": "DATOS SECUNDARIA", "price_ars": 1}
    with public_session() as session:
        primary, secondary = make_source(session), make_source(session)
        run_source(session, primary, now, StaticConnector([rec]))
        run_source(session, secondary, now, StaticConnector([dup]))
        report = run_source(session, secondary, now + timedelta(hours=2), StaticConnector([dup]))
        assert report.updated == 1
        load = session.scalar(
            select(Load).join(LoadSourceRef).where(LoadSourceRef.source_id == secondary.id)
        )
        assert load.description == "Datos de la primaria"
        assert load.price_ars == 1_000_000
        assert load.last_seen_at > now + timedelta(hours=1)  # la secundaria sí confirma vigencia
