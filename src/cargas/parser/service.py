"""Parser de mensajes: LLM local opcional (Ollama) validado con Pydantic + reglas determinísticas.

Siempre se ejecutan las reglas. Si el LLM está habilitado y responde algo válido, sus valores
tienen prioridad; cualquier campo que el LLM no aporte (o aporte mal) sale de las reglas. Si el
LLM falla por cualquier motivo, el resultado es el de las reglas y se informa la advertencia.
"""

import json
import logging
from datetime import date

import httpx
from pydantic import BaseModel, ValidationError

from cargas.config import get_settings
from cargas.geo.places import resolve_place
from cargas.parser.rules import parse_trip
from cargas.parser.schemas import ParseResult, TripDraft, VehicleType

log = logging.getLogger(__name__)


class LlmTripExtraction(BaseModel):
    """Esquema que se le pide al LLM. Se valida antes de usarlo."""

    origin: str | None = None
    destination: str | None = None
    departure_date: date | None = None
    vehicle_type: VehicleType | None = None
    refrigerated: bool | None = None
    pallets: int | None = None
    weight_kg: int | None = None
    empty: bool | None = None


_PROMPT = """Extraé los datos del viaje de un camionero argentino. Hoy es {today} ({weekday}).
Respondé SOLO con JSON con estas claves (null si el dato no aparece):
origin, destination (ciudades), departure_date (AAAA-MM-DD), vehicle_type
(semi|chasis|balancin|camioneta), refrigerated (bool), pallets (int), weight_kg (int),
empty (bool, true si vuelve sin carga).
Mensaje: {message}"""

_WEEKDAYS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


class LlmError(Exception):
    pass


class OllamaExtractor:
    def __init__(
        self, base_url: str, model: str, timeout: float, client: httpx.Client | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.client = client or httpx.Client(timeout=timeout)

    def extract(self, message: str, today: date) -> LlmTripExtraction:
        prompt = _PROMPT.format(
            today=today.isoformat(), weekday=_WEEKDAYS_ES[today.weekday()], message=message
        )
        try:
            resp = self.client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "format": LlmTripExtraction.model_json_schema(),
                    "stream": False,
                    "options": {"temperature": 0},
                },
            )
            resp.raise_for_status()
            content = resp.json()["message"]["content"]
            return LlmTripExtraction.model_validate(json.loads(content))
        except (httpx.HTTPError, KeyError, ValueError, ValidationError) as exc:
            raise LlmError(f"{type(exc).__name__}: {exc}") from exc


def _validated_llm_draft(extraction: LlmTripExtraction, today: date) -> tuple[TripDraft, list[str]]:
    """Descarta valores del LLM que no pasan las validaciones del dominio."""
    warnings: list[str] = []
    data = extraction.model_dump()
    for key in ("origin", "destination"):
        if data[key] is not None:
            place = resolve_place(data[key])
            if place is None:
                warnings.append(f"LLM: localidad desconocida '{data[key]}' descartada")
            data[key] = place.name if place else None
    if data["departure_date"] is not None and data["departure_date"] < today:
        warnings.append("LLM: fecha en el pasado descartada")
        data["departure_date"] = None
    try:
        return TripDraft(**data), warnings
    except ValidationError:
        warnings.append("LLM: capacidad fuera de rango descartada")
        data["pallets"] = data["weight_kg"] = None
        return TripDraft(**data), warnings


class MessageParser:
    def __init__(self, extractor: OllamaExtractor | None = None) -> None:
        self.extractor = extractor

    def parse(self, message: str, today: date) -> ParseResult:
        rules_draft = parse_trip(message, today)
        if self.extractor is None:
            return ParseResult(draft=rules_draft, method="rules")
        try:
            extraction = self.extractor.extract(message, today)
        except LlmError as exc:
            log.warning("LLM no disponible, se usan reglas: %s", exc)
            return ParseResult(
                draft=rules_draft, method="rules", warnings=[f"LLM no disponible ({exc})"]
            )
        llm_draft, warnings = _validated_llm_draft(extraction, today)
        # Las reglas completan lo que el LLM no aportó; la patente sólo la extraen las reglas.
        return ParseResult(
            draft=rules_draft.merged_with(llm_draft), method="llm", warnings=warnings
        )


def get_parser() -> MessageParser:
    settings = get_settings()
    if settings.llm_provider == "ollama":
        return MessageParser(
            OllamaExtractor(
                settings.ollama_url, settings.ollama_model, settings.llm_timeout_seconds
            )
        )
    return MessageParser()
