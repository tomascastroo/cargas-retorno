"""Agenda privada de dadores de carga de cada empresa.

- Alta manual o importación desde CSV (con detección de duplicados por teléfono o email).
- Al buscar carga para un viaje, se sugieren los contactos que cargan en el origen o sobre la
  ruta. En el MVP sólo se sugieren: el agente todavía no les escribe (ver docs/contactos.md).
"""

import csv
import io
import re
import uuid
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from cargas.geo.places import normalize, resolve_place
from cargas.geo.routing import Router
from cargas.matching.engine import TripSpec
from cargas.models import ShipperContact

CARGO_TYPES = ("general", "refrigerada", "granel", "peligrosa", "contenedor", "hacienda")
MAX_IMPORT_ROWS = 2000
MAX_IMPORT_BYTES = 1_000_000

# Encabezados aceptados en el CSV → campo. Se comparan normalizados (sin tildes ni mayúsculas).
_HEADERS = {
    "nombre": "name",
    "name": "name",
    "dador": "name",
    "empresa": "name",
    "telefono": "phone",
    "phone": "phone",
    "celular": "phone",
    "whatsapp": "phone",
    "email": "email",
    "mail": "email",
    "correo": "email",
    "localidades": "cities",
    "ciudades": "cities",
    "cities": "cities",
    "zonas": "cities",
    "mercaderias": "cargo_types",
    "mercaderia": "cargo_types",
    "cargo_types": "cargo_types",
    "notas": "notes",
    "notes": "notes",
    "observaciones": "notes",
}


class ContactData(BaseModel):
    """Validación común al alta manual y a cada fila del CSV."""

    name: str = Field(min_length=2, max_length=200)
    phone: str | None = None
    email: str | None = Field(default=None, max_length=320)
    cities: list[str] = Field(min_length=1, max_length=30)
    cargo_types: list[str] = []
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator("name", "notes")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return value.strip() if value else value

    @field_validator("phone")
    @classmethod
    def _phone(cls, value: str | None) -> str | None:
        if not value or not value.strip():
            return None
        digits = re.sub(r"\D", "", value)
        if not 8 <= len(digits) <= 15:
            raise ValueError("teléfono inválido (8 a 15 dígitos, idealmente con código de país)")
        return digits

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        if not value or not value.strip():
            return None
        value = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("email inválido")
        return value

    @field_validator("cities")
    @classmethod
    def _cities(cls, values: list[str]) -> list[str]:
        resolved, unknown = [], []
        for raw in values:
            if not raw.strip():
                continue
            place = resolve_place(raw)
            if place is None:
                unknown.append(raw.strip())
            elif place.name not in resolved:
                resolved.append(place.name)
        if unknown:
            raise ValueError(f"localidad desconocida: {', '.join(unknown)}")
        if not resolved:
            raise ValueError("indicá al menos una localidad donde carga")
        return resolved

    @field_validator("cargo_types")
    @classmethod
    def _cargo(cls, values: list[str]) -> list[str]:
        cleaned = [normalize(v) for v in values if v.strip()]
        invalid = [v for v in cleaned if v not in CARGO_TYPES]
        if invalid:
            raise ValueError(
                f"mercadería desconocida: {', '.join(invalid)} (válidas: {', '.join(CARGO_TYPES)})"
            )
        return list(dict.fromkeys(cleaned))

    @model_validator(mode="after")
    def _reachable(self) -> "ContactData":
        if not self.phone and not self.email:
            raise ValueError("indicá teléfono o email")
        return self


class ContactError(Exception):
    pass


def validate_contact(data: dict) -> ContactData:
    try:
        return ContactData.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        msg = str(first["msg"]).removeprefix("Value error, ")
        loc = ".".join(str(p) for p in first["loc"])
        raise ContactError(f"{loc}: {msg}" if loc else msg) from exc


def find_duplicate(
    session: Session, company_id: uuid.UUID, data: ContactData, exclude: uuid.UUID | None = None
) -> ShipperContact | None:
    """Contacto existente de la misma empresa con el mismo teléfono o email."""
    conditions = []
    if data.phone:
        conditions.append(ShipperContact.phone == data.phone)
    if data.email:
        conditions.append(ShipperContact.email == data.email)
    query = select(ShipperContact).where(ShipperContact.company_id == company_id, or_(*conditions))
    if exclude:
        query = query.where(ShipperContact.id != exclude)
    return session.scalars(query).first()


@dataclass
class ImportReport:
    created: int = 0
    updated: int = 0
    rejected: list[dict] = field(default_factory=list)  # {"line": n, "reason": "..."}


def _split(value: str | None) -> list[str]:
    return [v.strip() for v in re.split(r"[|;/]", value or "") if v.strip()]


def import_csv(session: Session, company_id: uuid.UUID, content: str) -> ImportReport:
    """Importa contactos desde CSV. Las filas con el mismo teléfono o email que un contacto
    existente lo actualizan (no se duplican). Las filas inválidas se informan y se saltean."""
    if len(content.encode()) > MAX_IMPORT_BYTES:
        raise ContactError("el archivo supera 1 MB")
    content = content.lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(content.split("\n", 1)[0], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(content), dialect=dialect)
    if not reader.fieldnames:
        raise ContactError("el archivo está vacío")
    mapping = {h: _HEADERS.get(normalize(h or "")) for h in reader.fieldnames}
    present = set(mapping.values())
    if "name" not in present or "cities" not in present or not present & {"phone", "email"}:
        raise ContactError("faltan columnas: se necesitan nombre, localidades y telefono o email")

    report = ImportReport()
    seen: set[str] = set()
    for line, row in enumerate(reader, start=2):
        if line - 1 > MAX_IMPORT_ROWS:
            report.rejected.append({"line": line, "reason": f"máximo {MAX_IMPORT_ROWS} filas"})
            break
        values = {mapping[k]: (v or "") for k, v in row.items() if k and mapping.get(k)}
        if not any(v.strip() for v in values.values()):
            continue  # fila vacía
        values["cities"] = _split(values.get("cities"))
        values["cargo_types"] = _split(values.get("cargo_types"))
        try:
            data = validate_contact(values)
        except ContactError as exc:
            report.rejected.append({"line": line, "reason": str(exc)})
            continue
        key = data.phone or data.email or ""
        if key in seen:
            report.rejected.append({"line": line, "reason": "repetido dentro del archivo"})
            continue
        seen.add(key)
        existing = find_duplicate(session, company_id, data)
        with session.begin_nested():
            if existing:
                apply(existing, data)
                report.updated += 1
            else:
                session.add(ShipperContact(company_id=company_id, **data.model_dump()))
                report.created += 1
            session.flush()
    return report


def apply(contact: ShipperContact, data: ContactData) -> None:
    for key, value in data.model_dump().items():
        setattr(contact, key, value)
    contact.active = True


@dataclass(frozen=True)
class ContactSuggestion:
    contact: ShipperContact
    city: str
    detour_km: float


def can_carry_any(cargo_types: list[str], trip: TripSpec, excluded: tuple[str, ...]) -> bool:
    if not cargo_types:
        return True
    return any(
        cargo not in excluded and (cargo != "refrigerada" or trip.refrigerated)
        for cargo in cargo_types
    )


def contacts_for_trip(
    session: Session,
    company_id: uuid.UUID,
    trip: TripSpec,
    router: Router,
    max_detour_km: int,
    excluded_cargo_types: tuple[str, ...] = (),
    limit: int = 5,
) -> list[ContactSuggestion]:
    """Contactos activos que cargan en el origen o en una localidad sobre la ruta y que mueven
    alguna mercadería que este camión puede llevar (sin datos de mercadería: se sugieren)."""
    base = router.distance(trip.origin, trip.destination).km
    best: dict[uuid.UUID, ContactSuggestion] = {}
    contacts = session.scalars(
        select(ShipperContact).where(
            ShipperContact.company_id == company_id, ShipperContact.active.is_(True)
        )
    ).all()
    for contact in contacts:
        if not can_carry_any(contact.cargo_types, trip, excluded_cargo_types):
            continue
        for city in contact.cities:
            place = resolve_place(city)
            if place is None or place.name == trip.destination_city:
                continue
            point = (place.lat, place.lon)
            detour = max(
                0.0,
                router.distance(trip.origin, point).km
                + router.distance(point, trip.destination).km
                - base,
            )
            if detour > max_detour_km:
                continue
            current = best.get(contact.id)
            if current is None or detour < current.detour_km:
                best[contact.id] = ContactSuggestion(contact, place.name, round(detour, 1))
    return sorted(best.values(), key=lambda s: (s.detour_km, s.contact.name))[:limit]
