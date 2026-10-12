"""Agente comercial: consulta disponibilidad y negocia el precio con el dador.

Reglas que nunca se rompen, con cualquier canal:
- No se pide ni se acepta un precio por debajo de la tarifa mínima del camionero.
- No se contraoferta más de `max_counter_uplift_pct` por encima de lo publicado, ni más de
  `max_negotiation_rounds` veces.
- El resultado es una propuesta (`awaiting_driver`): la reserva sólo se crea cuando el camionero
  la aprueba explícitamente.

En el MVP el único canal es `SimulatedShipperChannel`: responde según el campo `simulation` de
las cargas de demostración. No envía mensajes reales. Un canal real (WhatsApp, email, API de un
socio) implementará `ShipperChannel`; como su respuesta llega más tarde, deberá devolver
`ShipperReply(kind="pending")` y retomar la negociación cuando llegue la respuesta.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, Protocol

ReplyKind = Literal["accept", "counter", "unavailable", "reject", "pending"]


@dataclass(frozen=True)
class QuoteRequest:
    load_ref: str  # identificador de la oferta en la fuente
    description: str
    listed_price: Decimal
    ask_price: Decimal
    round: int
    simulation: dict[str, Any]


@dataclass(frozen=True)
class ShipperReply:
    kind: ReplyKind
    price: Decimal | None = None
    text: str = ""


class ShipperChannel(Protocol):
    name: str

    def request_quote(self, req: QuoteRequest) -> ShipperReply: ...


def _fmt(value: Decimal) -> str:
    return f"${value:,.0f}".replace(",", ".")


def _round_price(value: Decimal) -> Decimal:
    """Redondea a miles de pesos, como se cotiza un flete."""
    return (value / 1000).quantize(Decimal(1), rounding=ROUND_HALF_UP) * 1000


class SimulatedShipperChannel:
    """Dador simulado y determinístico. Comportamientos (campo `simulation.respuesta`):
    acepta | contraoferta (hasta `max_aumento_pct` sobre lo publicado) | no_disponible | rechaza.
    Sin datos de simulación, acepta el precio publicado.
    """

    name = "simulated"

    def request_quote(self, req: QuoteRequest) -> ShipperReply:
        behavior = req.simulation.get("respuesta", "acepta")
        if behavior == "no_disponible":
            return ShipperReply("unavailable", text="Ya la cubrimos con otro transporte, gracias.")
        if behavior == "rechaza":
            return ShipperReply("reject", text="Preferimos trabajar con transportes conocidos.")
        max_uplift = Decimal(str(req.simulation.get("max_aumento_pct", 0)))
        budget = _round_price(req.listed_price * (1 + max_uplift / 100))
        if req.ask_price <= budget:
            return ShipperReply(
                "accept", req.ask_price, f"Sigue disponible. Confirmamos {_fmt(req.ask_price)}."
            )
        return ShipperReply("counter", budget, f"Sigue disponible, pero llegamos a {_fmt(budget)}.")


@dataclass(frozen=True)
class NegotiationPolicy:
    floor_price: Decimal  # tarifa mínima del camionero para este recorrido cargado
    max_rounds: int
    max_counter_uplift_pct: float


@dataclass
class NegotiationOutcome:
    status: Literal["awaiting_driver", "unavailable", "rejected", "pending"]
    agreed_price: Decimal | None
    transcript: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""


def negotiate(
    req_base: QuoteRequest, policy: NegotiationPolicy, channel: ShipperChannel, now: datetime
) -> NegotiationOutcome:
    listed, floor = req_base.listed_price, policy.floor_price
    ceiling = _round_price(listed * Decimal(str(1 + policy.max_counter_uplift_pct / 100)))
    transcript: list[dict[str, Any]] = []

    def log(who: str, text: str, price: Decimal | None = None) -> None:
        transcript.append(
            {
                "de": who,
                "texto": text,
                "precio": str(price) if price else None,
                "canal": channel.name,
                "momento": now.isoformat(),
            }
        )

    if ceiling < floor:
        log(
            "agente",
            f"No se contacta: aun pidiendo {_fmt(ceiling)} "
            f"(+{policy.max_counter_uplift_pct:.0f} %) no se llega al mínimo "
            f"{_fmt(floor)}.",
        )
        return NegotiationOutcome(
            "rejected",
            None,
            transcript,
            f"La carga paga {_fmt(listed)} y tu mínimo es {_fmt(floor)}; "
            "no alcanza ni negociando dentro de tus límites.",
        )

    ask = max(listed, floor)
    for round_no in range(1, policy.max_rounds + 1):
        log(
            "agente",
            f"Hola, ¿sigue disponible '{req_base.description}'? Podemos llevarla por {_fmt(ask)}.",
            ask,
        )
        reply = channel.request_quote(
            QuoteRequest(
                req_base.load_ref, req_base.description, listed, ask, round_no, req_base.simulation
            )
        )
        log("dador", reply.text, reply.price)
        if reply.kind == "pending":
            return NegotiationOutcome("pending", None, transcript, "Esperando respuesta del dador.")
        if reply.kind == "unavailable":
            return NegotiationOutcome(
                "unavailable",
                None,
                transcript,
                "El dador informa que la carga ya no está disponible.",
            )
        if reply.kind == "reject":
            return NegotiationOutcome(
                "rejected",
                None,
                transcript,
                "El dador no aceptó trabajar con nosotros en esta carga.",
            )
        if reply.kind == "accept":
            return NegotiationOutcome(
                "awaiting_driver",
                ask,
                transcript,
                f"El dador confirma disponibilidad por {_fmt(ask)}.",
            )
        # Contraoferta.
        assert reply.price is not None
        if reply.price >= floor:
            log(
                "agente",
                f"Aceptamos {_fmt(reply.price)} sujeto a confirmación del camionero.",
                reply.price,
            )
            return NegotiationOutcome(
                "awaiting_driver",
                reply.price,
                transcript,
                f"El dador contraofertó {_fmt(reply.price)} (publicado {_fmt(listed)}).",
            )
        ask = min(ceiling, max(floor, _round_price((ask + reply.price) / 2)))
    log("agente", "No llegamos a un acuerdo dentro de los límites del camionero.")
    return NegotiationOutcome(
        "rejected", None, transcript, f"El dador no llega a tu mínimo de {_fmt(floor)}."
    )
