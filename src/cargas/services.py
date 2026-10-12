"""Acceso a datos compartido por el orquestador y la API."""

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from cargas.matching.engine import AgentPolicy, LoadSpec, TripSpec
from cargas.models import Agent, AuditEvent, Load, LoadSource, Trip, Truck


def audit(
    session: Session,
    company_id: uuid.UUID,
    actor: uuid.UUID | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    **data: Any,
) -> None:
    session.add(
        AuditEvent(
            company_id=company_id,
            actor_user_id=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            data=data,
        )
    )


def get_agent(session: Session, company_id: uuid.UUID) -> Agent:
    agent = session.scalar(select(Agent).where(Agent.company_id == company_id))
    if agent is None:  # toda empresa tiene un agente con valores por defecto
        agent = Agent(company_id=company_id, excluded_cargo_types=[], usual_routes=[])
        session.add(agent)
        session.flush()
    return agent


def agent_policy(agent: Agent) -> AgentPolicy:
    return AgentPolicy(
        min_rate_per_km=agent.min_rate_per_km,
        target_rate_per_km=agent.target_rate_per_km,
        cost_per_km=agent.cost_per_km,
        max_detour_km=agent.max_detour_km,
        date_flex_days=agent.date_flex_days,
        excluded_cargo_types=tuple(agent.excluded_cargo_types or ()),
    )


def trip_spec(trip: Trip, truck: Truck) -> TripSpec:
    return TripSpec(
        origin=(trip.origin_lat, trip.origin_lon),
        destination=(trip.dest_lat, trip.dest_lon),
        origin_city=trip.origin_city,
        destination_city=trip.dest_city,
        departure_date=trip.departure_date,
        vehicle_type=truck.vehicle_type,
        body_type=truck.body_type,
        refrigerated=truck.refrigerated,
        available_pallets=trip.available_pallets,
        available_kg=trip.available_kg,
    )


def load_spec(load: Load, source: LoadSource) -> LoadSpec:
    return LoadSpec(
        id=load.id,
        origin=(load.origin_lat, load.origin_lon),
        destination=(load.dest_lat, load.dest_lon),
        origin_city=load.origin_city,
        dest_city=load.dest_city,
        pickup_from=load.pickup_from,
        pickup_to=load.pickup_to,
        cargo_type=load.cargo_type,
        description=load.description,
        pallets=load.pallets,
        weight_kg=load.weight_kg,
        vehicle_types=tuple(load.vehicle_types or ()),
        body_types=tuple(load.body_types or ()),
        requires_refrigeration=load.requires_refrigeration,
        price_ars=load.price_ars,
        price_negotiable=load.price_negotiable,
        status=load.status,
        valid_until=load.valid_until,
        last_seen_at=load.last_seen_at,
        source_name=source.name,
        source_reliability=source.reliability,
        shipper_name=load.shipper_name,
    )


def candidate_loads(session: Session, trip: Trip) -> list[tuple[Load, LoadSource]]:
    """Ofertas cuya ventana de retiro no terminó antes de la salida (incluye vencidas o
    tomadas: el motor las descarta con su motivo, que se informa al camionero)."""
    rows = session.execute(
        select(Load, LoadSource)
        .join(LoadSource, Load.primary_source_id == LoadSource.id)
        .where(Load.pickup_to >= trip.departure_date - timedelta(days=1))
        .where(Load.status != "withdrawn")
    ).all()
    return [(load, source) for load, source in rows]


def load_snapshot(load: Load, source: LoadSource, now: datetime) -> dict[str, Any]:
    return {
        "origin": load.origin_city,
        "destination": load.dest_city,
        "pickup_from": load.pickup_from.isoformat(),
        "pickup_to": load.pickup_to.isoformat(),
        "cargo_type": load.cargo_type,
        "description": load.description,
        "pallets": load.pallets,
        "weight_kg": load.weight_kg,
        "price_ars": str(load.price_ars),
        "price_negotiable": load.price_negotiable,
        "shipper_name": load.shipper_name,
        "source": source.code,
        "source_is_demo": source.is_demo,
        "last_seen_at": load.last_seen_at.isoformat(),
        "captured_at": now.isoformat(),
    }
