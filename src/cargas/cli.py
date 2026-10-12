"""Comandos de operación.

python -m cargas.cli migrate   # alembic upgrade head
python -m cargas.cli seed      # fuentes demo + ingesta + empresas demo (idempotente)
python -m cargas.cli ingest    # vuelve a consultar las fuentes habilitadas
"""

import argparse
import logging
import os
import sys
from datetime import UTC, datetime

from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

from cargas.auth import hash_password
from cargas.config import project_root
from cargas.db import public_session, tenant_session
from cargas.ingestion.service import IngestionReport, run_ingestion
from cargas.models import Agent, Company, LoadSource, Truck, User
from cargas.services import audit

log = logging.getLogger("cargas.cli")

DEMO_SOURCES = [
    {
        "code": "dadores-demo",
        "name": "Dadores de carga (demo)",
        "kind": "json",
        "reliability": 0.9,
        "config": {"file": "dadores_demo.json"},
        "enabled": True,
    },
    {
        "code": "planilla-cooperativa-demo",
        "name": "Planilla de cooperativa (demo)",
        "kind": "csv",
        "reliability": 0.7,
        "config": {"file": "planilla_cooperativa_demo.csv"},
        "enabled": True,
    },
    # Ejemplo de integración HTTP con un socio. Deshabilitada: no existe una API pública gratuita
    # de cargas en Argentina que podamos usar legalmente (ver docs/fuentes.md).
    {
        "code": "socio-api-ejemplo",
        "name": "API de socio (ejemplo, deshabilitada)",
        "kind": "http",
        "reliability": 0.8,
        "config": {"url": "http://localhost:9/cargas", "timeout": 2},
        "enabled": False,
    },
]

DEMO_COMPANIES = [
    {
        "id": "11111111-1111-4111-8111-111111111111",
        "name": "Transportes del Centro (demo)",
        "cuit": "30-00000001-0",
        "email": "admin@transportesdelcentro.demo",
        "full_name": "Juan Pérez",
        "truck": {
            "plate": "AB123CD",
            "vehicle_type": "semi",
            "body_type": "sider",
            "capacity_pallets": 28,
            "capacity_kg": 30000,
        },
        "agent": {
            "excluded_cargo_types": ["peligrosa"],
            "usual_routes": [{"origin": "Buenos Aires", "destination": "Córdoba"}],
        },
    },
    {
        "id": "22222222-2222-4222-8222-222222222222",
        "name": "Logística Sur (demo)",
        "cuit": "30-00000002-0",
        "email": "admin@logisticasur.demo",
        "full_name": "Ana Gómez",
        "truck": {
            "plate": "AC456EF",
            "vehicle_type": "semi",
            "body_type": "termico",
            "capacity_pallets": 26,
            "capacity_kg": 28000,
            "refrigerated": True,
        },
        "agent": {},
    },
]


def migrate() -> None:
    root = project_root()
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(cfg, "head")


def _print_report(report: IngestionReport) -> None:
    for src in report.sources:
        line = (
            f"  {src.source}: {src.status} · leídas {src.fetched} · nuevas {src.created} · "
            f"actualizadas {src.updated} · duplicadas {src.duplicates} · "
            f"rechazadas {len(src.rejected)}"
        )
        print(line + (f" · error: {src.error}" if src.error else ""))
        for reason in src.rejected:
            print(f"      rechazo {reason}")
    print(f"  ofertas marcadas como vencidas: {report.expired}")


def ingest(now: datetime | None = None) -> IngestionReport:
    with public_session() as session:
        report = run_ingestion(session, now or datetime.now(UTC))
    _print_report(report)
    return report


def seed_sources() -> None:
    with public_session() as session:
        for spec in DEMO_SOURCES:
            source = session.scalar(select(LoadSource).where(LoadSource.code == spec["code"]))
            if source is None:
                session.add(LoadSource(is_demo=True, **spec))


def seed_companies(password: str) -> None:
    import uuid

    for spec in DEMO_COMPANIES:
        company_id = uuid.UUID(spec["id"])
        with tenant_session(company_id) as session:
            if session.get(Company, company_id) is not None:
                continue
            session.add(Company(id=company_id, name=spec["name"], cuit=spec["cuit"]))
            session.flush()
            user = User(
                company_id=company_id,
                email=spec["email"],
                full_name=spec["full_name"],
                password_hash=hash_password(password),
                role="admin",
            )
            session.add(user)
            session.flush()
            session.add(Truck(company_id=company_id, driver_user_id=user.id, **spec["truck"]))
            agent_data = {"excluded_cargo_types": [], "usual_routes": [], **spec["agent"]}
            session.add(Agent(company_id=company_id, **agent_data))
            audit(session, company_id, None, "company_seeded", "company", company_id)
        print(f"  empresa demo: {spec['name']} · usuario {spec['email']}")


def seed() -> None:
    password = os.environ.get("CARGAS_DEMO_PASSWORD", "demo1234")
    seed_sources()
    print("Ingesta de fuentes demo:")
    ingest()
    seed_companies(password)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(prog="cargas")
    parser.add_argument("command", choices=["migrate", "seed", "ingest", "check-db"])
    args = parser.parse_args(argv)
    if args.command == "migrate":
        migrate()
    elif args.command == "seed":
        seed()
    elif args.command == "ingest":
        ingest()
    elif args.command == "check-db":
        with public_session() as session:
            session.execute(text("SELECT 1"))
        print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
