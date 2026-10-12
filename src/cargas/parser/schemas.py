from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

VehicleType = Literal["semi", "chasis", "balancin", "camioneta"]
ESSENTIAL_FIELDS = ("origin", "destination", "departure_date")

FIELD_QUESTIONS = {
    "origin": "¿Desde qué ciudad salís?",
    "destination": "¿A qué ciudad volvés?",
    "departure_date": "¿Qué día salís? (por ejemplo: hoy, mañana, el viernes o 15/10)",
}


class TripDraft(BaseModel):
    """Datos de viaje extraídos de un mensaje. Todos opcionales: se completan en el diálogo."""

    origin: str | None = None  # nombre canónico del nomenclátor (cargas.geo.places)
    destination: str | None = None
    departure_date: date | None = None
    vehicle_type: VehicleType | None = None
    refrigerated: bool | None = None
    pallets: int | None = Field(default=None, ge=1, le=60)
    weight_kg: int | None = Field(default=None, ge=100, le=60_000)
    plate: str | None = None
    empty: bool | None = None

    def merged_with(self, newer: "TripDraft") -> "TripDraft":
        """Los valores del mensaje más nuevo reemplazan a los anteriores sólo si existen."""
        data = self.model_dump()
        data.update({k: v for k, v in newer.model_dump().items() if v is not None})
        return TripDraft(**data)

    def missing_essentials(self) -> list[str]:
        return [f for f in ESSENTIAL_FIELDS if getattr(self, f) is None]


class ParseResult(BaseModel):
    draft: TripDraft
    method: Literal["rules", "llm"]
    warnings: list[str] = []
