import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from cargas.api import schemas as s
from cargas.api.deps import company_context, current_claims, local_now, not_found, owned
from cargas.auth import TokenClaims, create_token, hash_password, verify_password
from cargas.channels.base import InboundMessage
from cargas.db import public_session, set_company, tenant_session
from cargas.geo.routing import get_router
from cargas.models import (
    Agent,
    AuditEvent,
    Booking,
    Company,
    Conversation,
    Load,
    LoadSource,
    LoadSourceRef,
    Match,
    Message,
    Negotiation,
    Trip,
    Truck,
    User,
)
from cargas.orchestrator import Orchestrator
from cargas.parser.service import get_parser
from cargas.services import audit, get_agent

router = APIRouter(prefix="/api")
Claims = Annotated[TokenClaims, Depends(current_claims)]


# --------------------------------------------------------------------------- cuentas


@router.post("/companies", response_model=s.TokenOut, status_code=201)
def signup(body: s.SignupIn) -> s.TokenOut:
    """Alta de una empresa de transporte con su usuario administrador y su agente."""
    company_id = uuid.uuid4()
    try:
        with tenant_session(company_id) as session:
            company = Company(id=company_id, name=body.company_name, cuit=body.cuit)
            session.add(company)
            session.flush()
            user = User(
                company_id=company_id,
                email=body.admin_email.lower(),
                full_name=body.admin_name,
                password_hash=hash_password(body.password),
                role="admin",
            )
            session.add(user)
            session.flush()
            get_agent(session, company_id)
            audit(
                session,
                company_id,
                user.id,
                "company_created",
                "company",
                company_id,
                name=company.name,
            )
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "El email ya está registrado") from exc
    return s.TokenOut(
        token=create_token(user.id, company_id),
        user_id=user.id,
        company_id=company_id,
        company_name=company.name,
    )


@router.post("/auth/login", response_model=s.TokenOut)
def login(body: s.LoginIn) -> s.TokenOut:
    with public_session() as session:
        # Única lectura entre empresas: buscar el usuario por email (política auth_lookup).
        session.execute(text("SELECT set_config('app.auth_lookup', 'on', true)"))
        user = session.scalar(select(User).where(User.email == body.email.lower()))
        if user is None or not verify_password(body.password, user.password_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Email o contraseña incorrectos")
        session.execute(text("SELECT set_config('app.auth_lookup', 'off', true)"))
        set_company(session, user.company_id)
        company = session.get(Company, user.company_id)
        assert company is not None
    return s.TokenOut(
        token=create_token(user.id, user.company_id),
        user_id=user.id,
        company_id=user.company_id,
        company_name=company.name,
    )


@router.get("/me", response_model=s.MeOut)
def me(claims: Claims) -> s.MeOut:
    with company_context(claims) as (session, user):
        company = session.get(Company, user.company_id)
        assert company is not None
        return s.MeOut(
            user_id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            company_id=company.id,
            company_name=company.name,
        )


# ---------------------------------------------------------------------- configuración


@router.get("/agent", response_model=s.AgentOut)
def get_agent_config(claims: Claims) -> Agent:
    with company_context(claims) as (session, user):
        return get_agent(session, user.company_id)


@router.put("/agent", response_model=s.AgentOut)
def update_agent_config(body: s.AgentIn, claims: Claims) -> Agent:
    with company_context(claims) as (session, user):
        agent = get_agent(session, user.company_id)
        for key, value in body.model_dump().items():
            setattr(agent, key, value)
        session.flush()
        audit(
            session,
            user.company_id,
            user.id,
            "agent_updated",
            "agent",
            agent.id,
            **body.model_dump(mode="json"),
        )
        session.refresh(agent)
        return agent


@router.get("/trucks", response_model=list[s.TruckOut])
def list_trucks(claims: Claims) -> list[Truck]:
    with company_context(claims) as (session, user):
        return list(
            session.scalars(
                select(Truck).where(Truck.company_id == user.company_id).order_by(Truck.plate)
            )
        )


@router.post("/trucks", response_model=s.TruckOut, status_code=201)
def create_truck(body: s.TruckIn, claims: Claims) -> Truck:
    try:
        with company_context(claims) as (session, user):
            data = body.model_dump()
            data["plate"] = data["plate"].replace(" ", "").upper()
            if data["body_type"] == "termico":
                data["refrigerated"] = True
            truck = Truck(company_id=user.company_id, **data)
            session.add(truck)
            session.flush()
            audit(
                session,
                user.company_id,
                user.id,
                "truck_created",
                "truck",
                truck.id,
                plate=truck.plate,
                capacity_pallets=truck.capacity_pallets,
            )
            return truck
    except IntegrityError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Ya existe un camión con esa patente"
        ) from exc


@router.delete("/trucks/{truck_id}", status_code=204)
def deactivate_truck(truck_id: uuid.UUID, claims: Claims) -> None:
    with company_context(claims) as (session, user):
        truck = owned(session, Truck, truck_id, user.company_id)
        truck.active = False
        audit(session, user.company_id, user.id, "truck_deactivated", "truck", truck.id)


# ------------------------------------------------------------------------------ chat


@router.post("/chat/messages", response_model=s.ChatOut)
def send_message(body: s.ChatIn, claims: Claims) -> s.ChatOut:
    """Canal de chat web. Un adaptador de WhatsApp llamaría al mismo orquestador."""
    now, today = local_now()
    with company_context(claims) as (session, user):
        if body.conversation_id:
            conversation = owned(session, Conversation, body.conversation_id, user.company_id)
            if conversation.user_id != user.id:
                raise not_found()
        else:
            conversation = Conversation(
                company_id=user.company_id, user_id=user.id, channel="web", state={}
            )
            session.add(conversation)
            session.flush()
        orchestrator = Orchestrator(get_parser(), get_router())
        replies = orchestrator.handle(
            session, user, conversation, InboundMessage(channel="web", text=body.text), now, today
        )
        return s.ChatOut(
            conversation_id=conversation.id,
            replies=[s.OutboundOut(text=r.text, payload=r.payload) for r in replies],
        )


@router.get("/conversations", response_model=list[s.ConversationOut])
def list_conversations(claims: Claims) -> list[Conversation]:
    with company_context(claims) as (session, user):
        return list(
            session.scalars(
                select(Conversation)
                .where(Conversation.company_id == user.company_id)
                .order_by(Conversation.updated_at.desc())
            )
        )


@router.get("/conversations/{conversation_id}/messages", response_model=list[s.MessageOut])
def conversation_messages(conversation_id: uuid.UUID, claims: Claims) -> list[Message]:
    with company_context(claims) as (session, user):
        owned(session, Conversation, conversation_id, user.company_id)
        return list(
            session.scalars(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.company_id == user.company_id,
                )
                .order_by(Message.created_at)
            )
        )


# ------------------------------------------------------------------------- historial


@router.get("/trips", response_model=list[s.TripOut])
def list_trips(claims: Claims) -> list[Trip]:
    with company_context(claims) as (session, user):
        return list(
            session.scalars(
                select(Trip)
                .where(Trip.company_id == user.company_id)
                .order_by(Trip.created_at.desc())
            )
        )


@router.get("/trips/{trip_id}", response_model=s.TripDetailOut)
def get_trip(trip_id: uuid.UUID, claims: Claims) -> s.TripDetailOut:
    with company_context(claims) as (session, user):
        trip = owned(session, Trip, trip_id, user.company_id)
        matches = session.scalars(
            select(Match)
            .where(Match.trip_id == trip.id, Match.company_id == user.company_id)
            .order_by(Match.rank)
        ).all()
        return s.TripDetailOut.model_validate(
            {
                **s.TripOut.model_validate(trip).model_dump(),
                "matches": [s.MatchOut.model_validate(m) for m in matches],
            }
        )


@router.get("/negotiations/{negotiation_id}", response_model=s.NegotiationOut)
def get_negotiation(negotiation_id: uuid.UUID, claims: Claims) -> Negotiation:
    with company_context(claims) as (session, user):
        return owned(session, Negotiation, negotiation_id, user.company_id)


@router.get("/bookings", response_model=list[s.BookingOut])
def list_bookings(claims: Claims) -> list[Booking]:
    with company_context(claims) as (session, user):
        return list(
            session.scalars(
                select(Booking)
                .where(Booking.company_id == user.company_id)
                .order_by(Booking.created_at.desc())
            )
        )


@router.get("/bookings/{booking_id}", response_model=s.BookingOut)
def get_booking(booking_id: uuid.UUID, claims: Claims) -> Booking:
    with company_context(claims) as (session, user):
        return owned(session, Booking, booking_id, user.company_id)


@router.get("/audit-events", response_model=list[s.AuditOut])
def list_audit(claims: Claims, limit: int = 100) -> list[AuditEvent]:
    with company_context(claims) as (session, user):
        return list(
            session.scalars(
                select(AuditEvent)
                .where(AuditEvent.company_id == user.company_id)
                .order_by(AuditEvent.created_at.desc())
                .limit(min(limit, 500))
            )
        )


# ------------------------------------------------------- oportunidades (compartidas)


@router.get("/loads", response_model=list[s.LoadOut])
def list_loads(claims: Claims, include_inactive: bool = False) -> list[s.LoadOut]:
    with company_context(claims) as (session, _user):
        query = select(Load).order_by(Load.pickup_from, Load.origin_city)
        if not include_inactive:
            query = query.where(Load.status == "available")
        loads = session.scalars(query).all()
        refs = session.execute(
            select(LoadSourceRef.load_id, LoadSource.code).join(
                LoadSource, LoadSource.id == LoadSourceRef.source_id
            )
        ).all()
        by_load: dict[uuid.UUID, list[str]] = {}
        for load_id, code in refs:
            by_load.setdefault(load_id, []).append(code)
        return [
            s.LoadOut.model_validate(load).model_copy(update={"sources": by_load.get(load.id, [])})
            for load in loads
        ]


@router.get("/sources", response_model=list[s.SourceOut])
def list_sources(claims: Claims) -> list[LoadSource]:
    with company_context(claims) as (session, _user):
        return list(session.scalars(select(LoadSource).order_by(LoadSource.code)))
