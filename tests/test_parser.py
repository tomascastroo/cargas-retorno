import json
from datetime import date

import httpx
import pytest

from cargas.parser.rules import parse_trip
from cargas.parser.service import MessageParser, OllamaExtractor

TODAY = date(2026, 10, 12)  # lunes


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "Estoy saliendo de Córdoba mañana para Buenos Aires. Vuelvo vacío con un semi de "
            "28 pallets.",
            {
                "origin": "Córdoba",
                "destination": "Buenos Aires",
                "departure_date": date(2026, 10, 13),
                "vehicle_type": "semi",
                "pallets": 28,
                "empty": True,
            },
        ),
        (
            "Vuelvo mañana de Córdoba a Buenos Aires vacío",
            {
                "origin": "Córdoba",
                "destination": "Buenos Aires",
                "departure_date": date(2026, 10, 13),
                "empty": True,
            },
        ),
        (
            "de vuelta a Rosario desde Mendoza el 20/10",
            {"origin": "Mendoza", "destination": "Rosario", "departure_date": date(2026, 10, 20)},
        ),
        (
            "salgo el viernes de rosario para mendoza, refrigerado 20 tn",
            {
                "origin": "Rosario",
                "destination": "Mendoza",
                "departure_date": date(2026, 10, 16),
                "refrigerated": True,
                "weight_kg": 20000,
            },
        ),
        (
            "estoy en villa maría, vuelvo a capital pasado mañana con el AB123CD",
            {
                "origin": "Villa María",
                "destination": "Buenos Aires",
                "departure_date": date(2026, 10, 14),
                "plate": "AB123CD",
            },
        ),
        (
            "CBA - BS AS hoy",
            {"origin": "Córdoba", "destination": "Buenos Aires", "departure_date": TODAY},
        ),
    ],
)
def test_rules_extract_trip(message: str, expected: dict) -> None:
    draft = parse_trip(message, TODAY)
    assert draft.model_dump(exclude_none=True) == expected


def test_rules_partial_message_reports_missing_essentials() -> None:
    draft = parse_trip("Vuelvo a Buenos Aires", TODAY)
    assert draft.destination == "Buenos Aires"
    assert draft.missing_essentials() == ["origin", "departure_date"]


def test_rules_unrelated_text_extracts_nothing() -> None:
    assert parse_trip("hola, ¿cómo andás?", TODAY).model_dump(exclude_none=True) == {}


def test_morning_time_is_not_tomorrow() -> None:
    assert parse_trip("salgo de Córdoba a la mañana", TODAY).departure_date is None


def test_date_without_year_in_the_past_rolls_to_next_year() -> None:
    assert parse_trip("de Rosario a Córdoba el 05/01", TODAY).departure_date == date(2027, 1, 5)


def _ollama(handler) -> OllamaExtractor:
    return OllamaExtractor(
        "http://ollama.test",
        "modelo",
        1.0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _reply(content: dict) -> httpx.Response:
    return httpx.Response(200, json={"message": {"content": json.dumps(content)}})


def test_llm_unavailable_falls_back_to_rules() -> None:
    def down(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    result = MessageParser(_ollama(down)).parse("Vuelvo mañana de Córdoba a Buenos Aires", TODAY)
    assert result.method == "rules"
    assert result.draft.origin == "Córdoba"
    assert "LLM no disponible" in result.warnings[0]


def test_llm_invalid_json_falls_back_to_rules() -> None:
    result = MessageParser(
        _ollama(lambda _r: httpx.Response(200, json={"message": {"content": "no es json"}}))
    ).parse("Vuelvo mañana de Córdoba a Buenos Aires", TODAY)
    assert result.method == "rules"
    assert result.draft.destination == "Buenos Aires"


def test_llm_output_is_validated_against_domain() -> None:
    # El LLM inventa una ciudad y una fecha pasada: se descartan y completan las reglas.
    result = MessageParser(
        _ollama(
            lambda _r: _reply(
                {
                    "origin": "Ciudad Gótica",
                    "destination": "bs as",
                    "departure_date": "2020-01-01",
                    "vehicle_type": "semi",
                    "pallets": 28,
                }
            )
        )
    ).parse("Vuelvo mañana de Córdoba a Buenos Aires", TODAY)
    assert result.method == "llm"
    assert result.draft.origin == "Córdoba"  # de las reglas
    assert result.draft.destination == "Buenos Aires"  # alias del LLM resuelto
    assert result.draft.departure_date == date(2026, 10, 13)  # de las reglas
    assert result.draft.pallets == 28
    assert len(result.warnings) == 2


def test_llm_fills_what_rules_miss() -> None:
    result = MessageParser(
        _ollama(
            lambda _r: _reply(
                {
                    "origin": "Córdoba",
                    "destination": "Buenos Aires",
                    "departure_date": "2026-10-15",
                }
            )
        )
    ).parse("Me vuelvo para Baires desde la Docta el jueves", TODAY)
    assert result.method == "llm"
    assert (result.draft.origin, result.draft.destination) == ("Córdoba", "Buenos Aires")
    assert result.draft.departure_date == date(2026, 10, 15)
