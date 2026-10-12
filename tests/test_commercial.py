from datetime import UTC, datetime
from decimal import Decimal

import pytest

from cargas.commercial.agent import (
    NegotiationPolicy,
    QuoteRequest,
    ShipperReply,
    SimulatedShipperChannel,
    negotiate,
)

NOW = datetime(2026, 10, 12, tzinfo=UTC)


def run(
    listed: int, floor: int, simulation: dict, rounds: int = 2, uplift: float = 15.0, channel=None
):
    return negotiate(
        QuoteRequest("L1", "Harina", Decimal(listed), Decimal(listed), 0, simulation),
        NegotiationPolicy(Decimal(floor), rounds, uplift),
        channel or SimulatedShipperChannel(),
        NOW,
    )


def test_accepts_listed_price_when_above_floor() -> None:
    out = run(1_900_000, 1_000_000, {"respuesta": "acepta"})
    assert out.status == "awaiting_driver"
    assert out.agreed_price == 1_900_000
    assert all(t["canal"] == "simulated" for t in out.transcript)


def test_listed_below_floor_asks_for_floor_and_accepts_counter_if_reaches_it() -> None:
    out = run(900_000, 975_000, {"respuesta": "contraoferta", "max_aumento_pct": 12})
    assert out.status == "awaiting_driver"
    assert out.agreed_price >= 975_000


def test_never_agrees_below_floor() -> None:
    out = run(900_000, 1_000_000, {"respuesta": "contraoferta", "max_aumento_pct": 5})
    assert out.status == "rejected"
    assert out.agreed_price is None


def test_does_not_contact_when_floor_is_unreachable_within_limits() -> None:
    out = run(500_000, 1_000_000, {"respuesta": "acepta"}, uplift=10)
    assert out.status == "rejected"
    assert len(out.transcript) == 1 and "No se contacta" in out.transcript[0]["texto"]


@pytest.mark.parametrize(
    ("behavior", "status"), [("no_disponible", "unavailable"), ("rechaza", "rejected")]
)
def test_shipper_unavailable_or_rejecting(behavior: str, status: str) -> None:
    assert run(1_500_000, 1_000_000, {"respuesta": behavior}).status == status


def test_respects_max_rounds() -> None:
    class Stubborn:
        name = "stub"
        calls = 0

        def request_quote(self, req: QuoteRequest) -> ShipperReply:
            Stubborn.calls += 1
            return ShipperReply("counter", Decimal(800_000), "no más")

    out = run(900_000, 1_000_000, {}, rounds=3, channel=Stubborn())
    assert out.status == "rejected"
    assert Stubborn.calls == 3


def test_pending_reply_from_async_channel_is_reported() -> None:
    class Async:
        name = "whatsapp"

        def request_quote(self, req: QuoteRequest) -> ShipperReply:
            return ShipperReply("pending", text="mensaje enviado")

    assert run(1_500_000, 1_000_000, {}, channel=Async()).status == "pending"
