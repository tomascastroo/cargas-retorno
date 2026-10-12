"""Motor de matching determinístico (sin LLM).

Dos etapas separadas:
1. `check_eligibility`: reglas duras. Una carga que no cumple alguna queda descartada con su
   motivo (código + texto).
2. `score`: puntaje 0-100 de las elegibles, como suma ponderada de componentes en [0, 1].
   Ver docs/matching.md para la fórmula y ejemplos.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from cargas.geo.routing import Router

WEIGHTS = {
    "rentabilidad": 0.40,
    "desvio": 0.20,
    "fecha": 0.15,
    "ocupacion": 0.10,
    "confiabilidad": 0.15,
}
# Antigüedad de la información: completa hasta FRESH_HOURS, decae linealmente hasta STALE_HOURS.
FRESH_HOURS = 12
STALE_HOURS = 72
# Más de esta antigüedad sin confirmación: la oferta "requiere verificación".
VERIFY_AFTER_HOURS = 48


@dataclass(frozen=True)
class TripSpec:
    origin: tuple[float, float]
    destination: tuple[float, float]
    origin_city: str
    destination_city: str
    departure_date: date
    vehicle_type: str
    body_type: str
    refrigerated: bool
    available_pallets: int
    available_kg: int


@dataclass(frozen=True)
class AgentPolicy:
    min_rate_per_km: Decimal
    target_rate_per_km: Decimal
    cost_per_km: Decimal
    max_detour_km: int
    date_flex_days: int
    excluded_cargo_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class LoadSpec:
    id: object
    origin: tuple[float, float]
    destination: tuple[float, float]
    origin_city: str
    dest_city: str
    pickup_from: date
    pickup_to: date
    cargo_type: str
    description: str
    pallets: int | None
    weight_kg: int | None
    vehicle_types: tuple[str, ...]
    body_types: tuple[str, ...]
    requires_refrigeration: bool
    price_ars: Decimal
    price_negotiable: bool
    status: str
    valid_until: datetime
    last_seen_at: datetime
    source_name: str
    source_reliability: float
    shipper_name: str


@dataclass
class Rejection:
    code: str
    reason: str


@dataclass
class RouteMetrics:
    base_km: float  # recorrido original vacío: origen → destino del camionero
    to_pickup_km: float  # origen del camionero → retiro de la carga
    loaded_km: float  # retiro → entrega
    to_destination_km: float  # entrega → destino del camionero
    detour_km: float
    distance_method: str

    @property
    def total_km(self) -> float:
        return self.to_pickup_km + self.loaded_km + self.to_destination_km


@dataclass
class Evaluation:
    load: LoadSpec
    route: RouteMetrics
    pickup_date: date | None
    floor_price: Decimal  # tarifa mínima del camionero para esta carga
    rejections: list[Rejection] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    components: dict[str, float] = field(default_factory=dict)
    score: float = 0.0
    net_gain: Decimal = Decimal(0)

    @property
    def eligible(self) -> bool:
        return not self.rejections


def route_metrics(trip: TripSpec, load: LoadSpec, router: Router) -> RouteMetrics:
    base = router.distance(trip.origin, trip.destination)
    to_pickup = router.distance(trip.origin, load.origin)
    loaded = router.distance(load.origin, load.destination)
    to_dest = router.distance(load.destination, trip.destination)
    detour = max(0.0, to_pickup.km + loaded.km + to_dest.km - base.km)
    methods = {d.method for d in (base, to_pickup, loaded, to_dest)}
    return RouteMetrics(
        base_km=base.km,
        to_pickup_km=to_pickup.km,
        loaded_km=loaded.km,
        to_destination_km=to_dest.km,
        detour_km=round(detour, 1),
        distance_method=", ".join(sorted(methods)),
    )


def _pickup_date(trip: TripSpec, load: LoadSpec, policy: AgentPolicy) -> date | None:
    """Primer día en que el camionero puede retirar: dentro de su ventana y la de la carga."""
    earliest = max(trip.departure_date, load.pickup_from)
    latest = min(trip.departure_date + timedelta(days=policy.date_flex_days), load.pickup_to)
    return earliest if earliest <= latest else None


def freshness(last_seen_at: datetime, now: datetime) -> float:
    age_h = (now - last_seen_at).total_seconds() / 3600
    if age_h <= FRESH_HOURS:
        return 1.0
    if age_h >= STALE_HOURS:
        return 0.0
    return 1 - (age_h - FRESH_HOURS) / (STALE_HOURS - FRESH_HOURS)


def check_eligibility(
    trip: TripSpec, load: LoadSpec, policy: AgentPolicy, router: Router, now: datetime
) -> Evaluation:
    route = route_metrics(trip, load, router)
    floor = (policy.min_rate_per_km * Decimal(str(route.loaded_km))).quantize(Decimal(1))
    ev = Evaluation(
        load=load, route=route, pickup_date=_pickup_date(trip, load, policy), floor_price=floor
    )
    reject = ev.rejections.append

    if load.status == "expired" or load.valid_until <= now:
        reject(Rejection("vencida", "la oferta está vencida"))
    elif load.status != "available":
        reject(Rejection("no_disponible", f"la oferta figura como '{load.status}'"))
    if ev.pickup_date is None:
        reject(
            Rejection(
                "fecha",
                f"retiro entre {load.pickup_from:%d/%m} y {load.pickup_to:%d/%m}"
                f", fuera de tu ventana de salida",
            )
        )
    if route.detour_km > policy.max_detour_km:
        reject(
            Rejection(
                "ruta", f"desvío de {route.detour_km:.0f} km (máximo {policy.max_detour_km} km)"
            )
        )
    if load.vehicle_types and trip.vehicle_type not in load.vehicle_types:
        reject(Rejection("vehiculo", f"pide {'/'.join(load.vehicle_types)}"))
    if load.body_types and trip.body_type not in load.body_types:
        reject(Rejection("carroceria", f"pide carrocería {'/'.join(load.body_types)}"))
    if load.requires_refrigeration and not trip.refrigerated:
        reject(Rejection("refrigeracion", "requiere equipo de frío"))
    if load.pallets and load.pallets > trip.available_pallets:
        reject(Rejection("capacidad", f"{load.pallets} pallets (tenés {trip.available_pallets})"))
    if load.weight_kg and load.weight_kg > trip.available_kg:
        reject(Rejection("capacidad", f"{load.weight_kg} kg (tenés {trip.available_kg} kg)"))
    if load.cargo_type in policy.excluded_cargo_types:
        reject(Rejection("mercaderia", f"mercadería '{load.cargo_type}' excluida por tu empresa"))
    if load.price_ars < floor:
        if load.price_negotiable:
            ev.flags.append("debajo_tarifa_minima")
        else:
            reject(
                Rejection(
                    "precio",
                    f"paga ${load.price_ars:,.0f} sin negociación, debajo de "
                    f"tu mínimo de ${floor:,.0f}".replace(",", "."),
                )
            )
    if (now - load.last_seen_at) > timedelta(hours=VERIFY_AFTER_HOURS):
        ev.flags.append("requiere_verificacion")
    return ev


def score(ev: Evaluation, trip: TripSpec, policy: AgentPolicy, now: datetime) -> Evaluation:
    """Completa componentes y puntaje de una evaluación elegible."""
    load, route = ev.load, ev.route
    # Ganancia neta incremental: lo que paga la carga menos el costo de los km extra.
    ev.net_gain = load.price_ars - policy.cost_per_km * Decimal(str(route.detour_km))
    reference = policy.target_rate_per_km * Decimal(str(max(route.base_km, 1.0)))
    profit = float(ev.net_gain / reference) if reference > 0 else 0.0

    days_late = (ev.pickup_date - trip.departure_date).days if ev.pickup_date else 99
    if load.pallets:
        occupancy = load.pallets / trip.available_pallets
    elif load.weight_kg:
        occupancy = load.weight_kg / trip.available_kg
    else:
        occupancy = 0.5  # sin dato de tamaño: neutro

    reliability = load.source_reliability * (0.3 + 0.7 * freshness(load.last_seen_at, now))
    ev.components = {
        "rentabilidad": round(min(max(profit, 0.0), 1.0), 3),
        "desvio": round(max(0.0, 1 - route.detour_km / max(policy.max_detour_km, 1)), 3),
        "fecha": round(max(0.0, 1 - days_late / (policy.date_flex_days + 1)), 3),
        "ocupacion": round(min(occupancy, 1.0), 3),
        "confiabilidad": round(reliability, 3),
    }
    total = sum(WEIGHTS[k] * v for k, v in ev.components.items())
    if "debajo_tarifa_minima" in ev.flags:
        total *= 0.8  # sólo sirve si la negociación llega al mínimo: menos probable
    ev.score = round(100 * total, 1)
    return ev


def _ars(value: Decimal | float) -> str:
    return f"${value:,.0f}".replace(",", ".")


def explain(ev: Evaluation, trip: TripSpec) -> str:
    load, route = ev.load, ev.route
    if route.detour_km < 15:
        path = "Está sobre tu recorrido"
    else:
        path = f"Desvío de {route.detour_km:.0f} km"
    parts = [
        f"{path} ({route.loaded_km:.0f} km cargado).",
        f"Paga {_ars(load.price_ars)}"
        + (
            f"; descontando el costo del desvío te deja {_ars(ev.net_gain)} más que volver vacío."
            if route.detour_km >= 1
            else ", todo extra frente a volver vacío."
        ),
    ]
    if ev.pickup_date == trip.departure_date:
        parts.append(f"Retiro el mismo día que salís, en {load.origin_city}.")
    elif ev.pickup_date:
        parts.append(f"Retiro el {ev.pickup_date:%d/%m} en {load.origin_city}.")
    if load.pallets:
        parts.append(f"Usa {load.pallets} de {trip.available_pallets} pallets.")
    if "debajo_tarifa_minima" in ev.flags:
        parts.append(f"Paga menos que tu mínimo ({_ars(ev.floor_price)}): hay que negociar.")
    if "requiere_verificacion" in ev.flags:
        parts.append("La oferta no se confirmó en las últimas 48 h: hay que verificarla.")
    return " ".join(parts)


def rank(
    trip: TripSpec, loads: list[LoadSpec], policy: AgentPolicy, router: Router, now: datetime
) -> tuple[list[Evaluation], list[Evaluation]]:
    """Devuelve (elegibles ordenadas por puntaje, descartadas con sus motivos)."""
    evaluations = [check_eligibility(trip, load, policy, router, now) for load in loads]
    eligible = [score(ev, trip, policy, now) for ev in evaluations if ev.eligible]
    eligible.sort(key=lambda ev: (-ev.score, -ev.load.price_ars))
    rejected = [ev for ev in evaluations if not ev.eligible]
    return eligible, rejected
