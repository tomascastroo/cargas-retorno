import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Email = Annotated[str, Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SignupIn(BaseModel):
    company_name: str = Field(min_length=2, max_length=200)
    cuit: str | None = Field(default=None, max_length=20)
    admin_name: str = Field(min_length=2, max_length=200)
    admin_email: Email
    password: str = Field(min_length=8, max_length=200)


class LoginIn(BaseModel):
    email: Email
    password: str


class TokenOut(BaseModel):
    token: str
    user_id: uuid.UUID
    company_id: uuid.UUID
    company_name: str


class MeOut(BaseModel):
    user_id: uuid.UUID
    email: str
    full_name: str
    role: str
    company_id: uuid.UUID
    company_name: str


class TruckIn(BaseModel):
    plate: str = Field(min_length=6, max_length=10, pattern=r"^[A-Za-z0-9 ]+$")
    vehicle_type: Literal["semi", "chasis", "balancin", "camioneta"]
    body_type: Literal["sider", "caja seca", "playo", "tolva", "termico"] = "sider"
    capacity_pallets: int = Field(ge=1, le=60)
    capacity_kg: int = Field(ge=500, le=60_000)
    refrigerated: bool = False


class TruckOut(ORM):
    id: uuid.UUID
    plate: str
    vehicle_type: str
    body_type: str
    capacity_pallets: int
    capacity_kg: int
    refrigerated: bool
    active: bool


class AgentIn(BaseModel):
    name: str = Field(default="Agente de retorno", max_length=100)
    min_rate_per_km: Decimal = Field(ge=0, le=1_000_000)
    target_rate_per_km: Decimal = Field(gt=0, le=1_000_000)
    cost_per_km: Decimal = Field(ge=0, le=1_000_000)
    max_detour_km: int = Field(ge=0, le=2000)
    date_flex_days: int = Field(ge=0, le=7)
    excluded_cargo_types: list[str] = []
    usual_routes: list[dict[str, str]] = []
    max_negotiation_rounds: int = Field(ge=1, le=5)
    max_counter_uplift_pct: float = Field(ge=0, le=50)


class AgentOut(ORM, AgentIn):
    id: uuid.UUID
    updated_at: datetime


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    conversation_id: uuid.UUID | None = None


class OutboundOut(BaseModel):
    text: str
    payload: dict[str, Any] = {}


class ChatOut(BaseModel):
    conversation_id: uuid.UUID
    replies: list[OutboundOut]


class MessageOut(ORM):
    id: uuid.UUID
    direction: str
    text: str
    payload: dict[str, Any]
    created_at: datetime


class ConversationOut(ORM):
    id: uuid.UUID
    channel: str
    state: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class MatchOut(ORM):
    id: uuid.UUID
    load_id: uuid.UUID
    rank: int
    score: float
    components: dict[str, Any]
    metrics: dict[str, Any]
    explanation: str
    load_snapshot: dict[str, Any]
    status: str


class TripOut(ORM):
    id: uuid.UUID
    truck_id: uuid.UUID
    origin_city: str
    dest_city: str
    departure_date: date
    available_pallets: int
    available_kg: int
    parse_method: str
    raw_text: str | None
    status: str
    created_at: datetime


class TripDetailOut(TripOut):
    matches: list[MatchOut]


class NegotiationOut(ORM):
    id: uuid.UUID
    match_id: uuid.UUID
    channel: str
    status: str
    floor_price: Decimal
    listed_price: Decimal
    agreed_price: Decimal | None
    transcript: list[dict[str, Any]]
    created_at: datetime


class BookingOut(ORM):
    id: uuid.UUID
    trip_id: uuid.UUID
    match_id: uuid.UUID
    load_id: uuid.UUID
    negotiation_id: uuid.UUID
    agreed_price: Decimal
    status: str
    approved_at: datetime


class AuditOut(ORM):
    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    data: dict[str, Any]
    created_at: datetime


class LoadOut(ORM):
    id: uuid.UUID
    origin_city: str
    dest_city: str
    pickup_from: date
    pickup_to: date
    cargo_type: str
    description: str
    pallets: int | None
    weight_kg: int | None
    price_ars: Decimal
    price_negotiable: bool
    shipper_name: str
    status: str
    valid_until: datetime
    last_seen_at: datetime
    sources: list[str] = []


class SourceOut(ORM):
    code: str
    name: str
    kind: str
    reliability: float
    is_demo: bool
    enabled: bool
    last_run_at: datetime | None
    last_status: str | None
    last_error: str | None
    last_count: int | None
