"""Normalización de registros crudos al esquema común y huella para detectar duplicados."""

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from cargas.geo.places import Place, normalize, resolve_place

CargoType = Literal["general", "refrigerada", "granel", "peligrosa", "contenedor", "hacienda"]


class RawLoad(BaseModel):
    """Esquema de entrada común a todos los conectores."""

    external_id: str
    origin: str
    destination: str
    pickup_from: date
    pickup_to: date | None = None
    cargo_type: CargoType = "general"
    description: str = Field(max_length=300)
    pallets: int | None = Field(default=None, ge=1, le=60)
    weight_kg: int | None = Field(default=None, ge=1, le=60_000)
    vehicle_types: list[str] = []
    body_types: list[str] = []
    requires_refrigeration: bool = False
    price_ars: Decimal = Field(gt=0)
    price_negotiable: bool = True
    shipper_name: str
    shipper_contact: str | None = None
    status: Literal["available", "taken", "withdrawn"] = "available"
    valid_until: datetime
    published_at: datetime | None = None
    simulation: dict[str, Any] = {}  # sólo datos demo: comportamiento del dador simulado

    @field_validator("vehicle_types", "body_types")
    @classmethod
    def _lower(cls, values: list[str]) -> list[str]:
        return [normalize(v) for v in values]

    @field_validator("price_ars", mode="before")
    @classmethod
    def _price(cls, value: Any) -> Any:
        # Acepta "1.900.000" o "1900000,50" (formato argentino) además de números.
        if isinstance(value, str):
            value = value.replace("$", "").replace(" ", "")
            if "," in value or value.count(".") > 1:
                value = value.replace(".", "").replace(",", ".")
        return value


@dataclass(frozen=True)
class NormalizedLoad:
    raw: RawLoad
    origin: Place
    destination: Place
    fingerprint: str


class NormalizationError(Exception):
    pass


def _shipper_key(name: str) -> str:
    """'Molinos del Centro S.A.' y 'MOLINOS DEL CENTRO SA' son el mismo dador."""
    key = re.sub(r"[^a-z0-9 ]", "", normalize(name))
    key = re.sub(r"\b(sa|srl|sas|sociedad anonima)\b", "", key)
    return re.sub(r"\s+", " ", key).strip()


def fingerprint(
    origin: Place,
    destination: Place,
    pickup_from: date,
    cargo_type: str,
    shipper: str,
    pallets: int | None,
    weight_kg: int | None,
) -> str:
    """Misma oferta = mismo recorrido, fecha de retiro, tipo, dador y tamaño.

    El precio queda afuera a propósito: dos fuentes pueden publicar la misma carga con precios
    distintos (o el dador actualizarlo); eso es una actualización, no otra carga.
    """
    key = "|".join(
        [
            normalize(origin.name),
            normalize(destination.name),
            pickup_from.isoformat(),
            cargo_type,
            _shipper_key(shipper),
            str(pallets or ""),
            str(round((weight_kg or 0) / 500)),
        ]
    )
    return hashlib.sha256(key.encode()).hexdigest()


def normalize_record(record: dict[str, Any]) -> NormalizedLoad:
    try:
        raw = RawLoad.model_validate(record)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"])
        raise NormalizationError(f"campo '{loc}': {first['msg']}") from exc
    origin, destination = resolve_place(raw.origin), resolve_place(raw.destination)
    if origin is None or destination is None:
        unknown = raw.origin if origin is None else raw.destination
        raise NormalizationError(f"localidad desconocida: '{unknown}'")
    if raw.pickup_to is None:
        raw.pickup_to = raw.pickup_from
    if raw.pickup_to < raw.pickup_from:
        raise NormalizationError("pickup_to anterior a pickup_from")
    if raw.valid_until.tzinfo is None:
        raise NormalizationError("valid_until sin zona horaria")
    return NormalizedLoad(
        raw=raw,
        origin=origin,
        destination=destination,
        fingerprint=fingerprint(
            origin,
            destination,
            raw.pickup_from,
            raw.cargo_type,
            raw.shipper_name,
            raw.pallets,
            raw.weight_kg,
        ),
    )
