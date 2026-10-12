"""Modelo de datos (SQLAlchemy 2).

Tablas privadas por empresa: llevan `company_id` y Row-Level Security (ver migración inicial).
Tablas compartidas (oportunidades públicas): `load_sources`, `loads`, `load_source_refs`.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _company_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


# --------------------------------------------------------------------------- empresas


class Company(Base):
    __tablename__ = "companies"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200))
    cuit: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = _created()


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = _company_fk()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20), default="admin")  # admin | driver
    phone: Mapped[str | None] = mapped_column(String(30))  # futuro canal WhatsApp
    created_at: Mapped[datetime] = _created()


class Agent(Base):
    """Configuración del agente de la empresa: un registro de datos, no un proceso."""

    __tablename__ = "agents"
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), unique=True
    )
    name: Mapped[str] = mapped_column(String(100), default="Agente de retorno")
    # Tarifa mínima por km cargado (ARS). Ninguna negociación cierra por debajo.
    min_rate_per_km: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("1500"))
    # Tarifa objetivo por km cargado: 100 % del componente de rentabilidad.
    target_rate_per_km: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("2600"))
    # Costo variable por km (combustible, desgaste), usado para el costo del desvío.
    cost_per_km: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("900"))
    max_detour_km: Mapped[int] = mapped_column(Integer, default=200)
    date_flex_days: Mapped[int] = mapped_column(Integer, default=1)
    excluded_cargo_types: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    # Rutas habituales: [{"origin": "Córdoba", "destination": "Buenos Aires"}]
    usual_routes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    max_negotiation_rounds: Mapped[int] = mapped_column(Integer, default=2)
    # Cuánto por encima de la oferta publicada puede pedir el agente al contraofertar.
    max_counter_uplift_pct: Mapped[float] = mapped_column(Float, default=15.0)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Truck(Base):
    __tablename__ = "trucks"
    __table_args__ = (UniqueConstraint("company_id", "plate"),)
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = _company_fk()
    plate: Mapped[str] = mapped_column(String(15))
    vehicle_type: Mapped[str] = mapped_column(String(20))  # semi | chasis | balancin | camioneta
    body_type: Mapped[str] = mapped_column(String(20), default="sider")
    capacity_pallets: Mapped[int] = mapped_column(Integer)
    capacity_kg: Mapped[int] = mapped_column(Integer)
    refrigerated: Mapped[bool] = mapped_column(Boolean, default=False)
    driver_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _created()


class ShipperContact(Base):
    """Dador de carga con el que trabaja la empresa (agenda privada)."""

    __tablename__ = "shipper_contacts"
    __table_args__ = (
        Index(
            "uq_shipper_contacts_phone",
            "company_id",
            "phone",
            unique=True,
            postgresql_where=text("phone IS NOT NULL"),
        ),
        Index(
            "uq_shipper_contacts_email",
            "company_id",
            "email",
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = _company_fk()
    name: Mapped[str] = mapped_column(String(200))
    # Sólo dígitos, con código de país (p. ej. 5493511234567). Clave para el futuro WhatsApp.
    phone: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(320))
    # Localidades donde carga (nombres canónicos del nomenclátor).
    cities: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    cargo_types: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# --------------------------------------------------------------- oportunidades (compartidas)


class LoadSource(Base):
    __tablename__ = "load_sources"
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))  # fixture | csv | http
    # Confiabilidad 0..1 asignada a la fuente (forma parte del puntaje).
    reliability: Mapped[float] = mapped_column(Float, default=0.7)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(20))  # ok | error
    last_error: Mapped[str | None] = mapped_column(Text)
    last_count: Mapped[int | None] = mapped_column(Integer)


class Load(Base):
    __tablename__ = "loads"
    id: Mapped[uuid.UUID] = _pk()
    # Huella normalizada para detectar la misma oferta publicada en distintas fuentes.
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    origin_city: Mapped[str] = mapped_column(String(100))
    origin_province: Mapped[str] = mapped_column(String(100))
    origin_lat: Mapped[float] = mapped_column(Float)
    origin_lon: Mapped[float] = mapped_column(Float)
    dest_city: Mapped[str] = mapped_column(String(100))
    dest_province: Mapped[str] = mapped_column(String(100))
    dest_lat: Mapped[float] = mapped_column(Float)
    dest_lon: Mapped[float] = mapped_column(Float)
    pickup_from: Mapped[date] = mapped_column(Date)
    pickup_to: Mapped[date] = mapped_column(Date)
    cargo_type: Mapped[str] = mapped_column(String(30))  # general | refrigerada | granel | ...
    description: Mapped[str] = mapped_column(String(300))
    pallets: Mapped[int | None] = mapped_column(Integer)
    weight_kg: Mapped[int | None] = mapped_column(Integer)
    # Vacío = cualquier tipo de vehículo / carrocería.
    vehicle_types: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    body_types: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    requires_refrigeration: Mapped[bool] = mapped_column(Boolean, default=False)
    price_ars: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    price_negotiable: Mapped[bool] = mapped_column(Boolean, default=True)
    shipper_name: Mapped[str] = mapped_column(String(200))
    shipper_contact: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="available", index=True)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Última vez que alguna fuente confirmó la oferta: base de la "frescura".
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    primary_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("load_sources.id")
    )
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class LoadSourceRef(Base):
    """Cada vez que una fuente publica una oferta (trazabilidad y duplicados)."""

    __tablename__ = "load_source_refs"
    __table_args__ = (UniqueConstraint("source_id", "external_id"),)
    id: Mapped[uuid.UUID] = _pk()
    load_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("loads.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("load_sources.id"))
    external_id: Mapped[str] = mapped_column(String(120))
    price_ars: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------- operación (por empresa)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = _company_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    channel: Mapped[str] = mapped_column(String(20), default="web")  # web | whatsapp (futuro)
    # Estado del diálogo: borrador del viaje, datos pendientes, opciones mostradas.
    state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = _company_fk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    direction: Mapped[str] = mapped_column(String(3))  # in | out
    text: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


class Trip(Base):
    __tablename__ = "trips"
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = _company_fk()
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    truck_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("trucks.id"))
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    origin_city: Mapped[str] = mapped_column(String(100))
    origin_lat: Mapped[float] = mapped_column(Float)
    origin_lon: Mapped[float] = mapped_column(Float)
    dest_city: Mapped[str] = mapped_column(String(100))
    dest_lat: Mapped[float] = mapped_column(Float)
    dest_lon: Mapped[float] = mapped_column(Float)
    departure_date: Mapped[date] = mapped_column(Date)
    available_pallets: Mapped[int] = mapped_column(Integer)
    available_kg: Mapped[int] = mapped_column(Integer)
    raw_text: Mapped[str | None] = mapped_column(Text)
    parse_method: Mapped[str] = mapped_column(String(20))  # rules | llm
    # searching | options_sent | no_results | booked | closed
    status: Mapped[str] = mapped_column(String(20), default="searching")
    created_at: Mapped[datetime] = _created()


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (UniqueConstraint("trip_id", "load_id"),)
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = _company_fk()
    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), index=True
    )
    load_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("loads.id"))
    rank: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(Float)
    components: Mapped[dict[str, Any]] = mapped_column(JSONB)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    explanation: Mapped[str] = mapped_column(Text)
    # Copia de la oferta al momento del match: la oferta pública puede cambiar después.
    load_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="proposed")
    # proposed | discarded | selected | booked | unavailable | rejected
    created_at: Mapped[datetime] = _created()


class Negotiation(Base):
    __tablename__ = "negotiations"
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = _company_fk()
    match_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("matches.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[str] = mapped_column(String(20), default="simulated")
    # awaiting_driver | unavailable | rejected | confirmed | declined_by_driver
    status: Mapped[str] = mapped_column(String(30))
    floor_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    listed_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    agreed_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    transcript: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Booking(Base):
    __tablename__ = "bookings"
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = _company_fk()
    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trips.id"), unique=True
    )
    match_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("matches.id"))
    negotiation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("negotiations.id")
    )
    load_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("loads.id"))
    agreed_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    # simulated_confirmed: aprobada por el camionero, el contacto con el dador fue simulado.
    status: Mapped[str] = mapped_column(String(30), default="simulated_confirmed")
    approved_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class AuditEvent(Base):
    """Registro inmutable (un trigger impide UPDATE y DELETE)."""

    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = _pk()
    company_id: Mapped[uuid.UUID] = _company_fk()
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )


TENANT_TABLES = (
    "users",
    "agents",
    "trucks",
    "shipper_contacts",
    "conversations",
    "messages",
    "trips",
    "matches",
    "negotiations",
    "bookings",
    "audit_events",
)
