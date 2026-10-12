from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from cargas.geo.places import resolve_place
from cargas.geo.routing import HaversineRouter
from cargas.matching.engine import WEIGHTS, AgentPolicy, LoadSpec, TripSpec, check_eligibility, rank

NOW = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)
TOMORROW = date(2026, 10, 13)
ROUTER = HaversineRouter(1.12)


def point(city: str) -> tuple[float, float]:
    place = resolve_place(city)
    assert place is not None
    return place.lat, place.lon


TRIP = TripSpec(
    origin=point("Córdoba"),
    destination=point("Buenos Aires"),
    origin_city="Córdoba",
    destination_city="Buenos Aires",
    departure_date=TOMORROW,
    vehicle_type="semi",
    body_type="sider",
    refrigerated=False,
    available_pallets=28,
    available_kg=30000,
)
POLICY = AgentPolicy(
    min_rate_per_km=Decimal(1500),
    target_rate_per_km=Decimal(2600),
    cost_per_km=Decimal(900),
    max_detour_km=200,
    date_flex_days=1,
    excluded_cargo_types=("peligrosa",),
)


def load(origin: str = "Córdoba", dest: str = "Buenos Aires", **kw) -> LoadSpec:
    base = dict(
        id=f"{origin}-{dest}",
        origin=point(origin),
        destination=point(dest),
        origin_city=origin,
        dest_city=dest,
        pickup_from=TOMORROW,
        pickup_to=TOMORROW,
        cargo_type="general",
        description="Carga",
        pallets=24,
        weight_kg=20000,
        vehicle_types=(),
        body_types=(),
        requires_refrigeration=False,
        price_ars=Decimal(1_900_000),
        price_negotiable=True,
        status="available",
        valid_until=NOW + timedelta(days=2),
        last_seen_at=NOW - timedelta(hours=1),
        source_name="demo",
        source_reliability=0.9,
        shipper_name="Dador",
    )
    base.update(kw)
    return LoadSpec(**base)


def codes(spec: LoadSpec) -> list[str]:
    return [r.code for r in check_eligibility(TRIP, spec, POLICY, ROUTER, NOW).rejections]


def test_load_on_the_same_route_is_eligible_without_detour() -> None:
    ev = check_eligibility(TRIP, load(), POLICY, ROUTER, NOW)
    assert ev.eligible
    assert ev.route.detour_km == 0
    assert 650 < ev.route.loaded_km < 780  # ~700 km reales por ruta


@pytest.mark.parametrize(
    ("spec", "code"),
    [
        (load(dest="Mendoza"), "ruta"),
        (load(valid_until=NOW - timedelta(hours=1)), "vencida"),
        (load(status="expired"), "vencida"),
        (load(status="taken"), "no_disponible"),
        (
            load(pickup_from=TOMORROW + timedelta(days=5), pickup_to=TOMORROW + timedelta(days=6)),
            "fecha",
        ),
        (
            load(pickup_from=TOMORROW - timedelta(days=3), pickup_to=TOMORROW - timedelta(days=2)),
            "fecha",
        ),
        (load(pallets=32), "capacidad"),
        (load(weight_kg=31000), "capacidad"),
        (load(vehicle_types=("chasis",)), "vehiculo"),
        (load(body_types=("tolva",)), "carroceria"),
        (load(requires_refrigeration=True), "refrigeracion"),
        (load(cargo_type="peligrosa"), "mercaderia"),
        (load(price_ars=Decimal(500_000), price_negotiable=False), "precio"),
    ],
)
def test_incompatible_loads_are_rejected_with_reason(spec: LoadSpec, code: str) -> None:
    assert code in codes(spec)


def test_pickup_within_flex_days_is_eligible_but_scores_lower() -> None:
    later = load(pickup_from=TOMORROW + timedelta(days=1), pickup_to=TOMORROW + timedelta(days=1))
    eligible, _ = rank(TRIP, [load(), later], POLICY, ROUTER, NOW)
    assert [ev.load for ev in eligible] == [load(), later]
    assert eligible[1].components["fecha"] < eligible[0].components["fecha"] == 1.0


def test_low_negotiable_price_is_flagged_not_rejected() -> None:
    ev = check_eligibility(TRIP, load(price_ars=Decimal(500_000)), POLICY, ROUTER, NOW)
    assert ev.eligible
    assert "debajo_tarifa_minima" in ev.flags


def test_stale_offer_requires_verification_and_loses_reliability() -> None:
    stale = load(last_seen_at=NOW - timedelta(hours=60))
    eligible, _ = rank(TRIP, [stale, load(id="fresh")], POLICY, ROUTER, NOW)
    assert "requiere_verificacion" in eligible[-1].flags
    assert eligible[-1].load.id == stale.id
    assert eligible[-1].components["confiabilidad"] < eligible[0].components["confiabilidad"]


def test_ranking_prefers_better_paying_load_on_route() -> None:
    cheap = load(id="barata", price_ars=Decimal(1_200_000))
    good = load(id="buena", price_ars=Decimal(1_900_000))
    eligible, _ = rank(TRIP, [cheap, good], POLICY, ROUTER, NOW)
    assert [ev.load.id for ev in eligible] == ["buena", "barata"]


def test_detour_reduces_score_even_with_same_price() -> None:
    on_route = load(id="directa")
    detour = load("San Francisco", "Buenos Aires", id="desvio")
    eligible, _ = rank(TRIP, [detour, on_route], POLICY, ROUTER, NOW)
    assert [ev.load.id for ev in eligible] == ["directa", "desvio"]
    assert eligible[1].route.detour_km > 30
    assert eligible[1].net_gain < eligible[0].net_gain


def test_score_is_weighted_sum_of_components() -> None:
    (ev,), _ = rank(TRIP, [load()], POLICY, ROUTER, NOW)
    assert set(ev.components) == set(WEIGHTS)
    expected = round(100 * sum(WEIGHTS[k] * v for k, v in ev.components.items()), 1)
    assert ev.score == pytest.approx(expected, abs=0.2)
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


def test_no_eligible_loads_returns_all_rejections() -> None:
    eligible, rejected = rank(
        replace(TRIP, available_pallets=5), [load(), load(pallets=10)], POLICY, ROUTER, NOW
    )
    assert eligible == []
    assert len(rejected) == 2
