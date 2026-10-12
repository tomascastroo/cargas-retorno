"""Distancias por ruta.

- `HaversineRouter`: distancia en línea recta × factor de ruta (1,12 por defecto, calibrado a
  mano con Córdoba–Buenos Aires ≈ 700 km y Rosario–Buenos Aires ≈ 300 km). No requiere red;
  error esperable ±10-15 % frente a la distancia real por ruta, mayor en zonas de montaña.
- `OsrmRouter`: consulta un servidor OSRM propio (open source, se levanta con el extracto de
  Argentina de Geofabrik). Si falla, usa el estimador haversine y lo informa en `method`.
"""

import logging
import math
from dataclasses import dataclass
from typing import Protocol

import httpx

from cargas.config import get_settings

log = logging.getLogger(__name__)

Point = tuple[float, float]  # (lat, lon)


@dataclass(frozen=True)
class Distance:
    km: float
    method: str


class Router(Protocol):
    def distance(self, a: Point, b: Point) -> Distance: ...


def haversine_km(a: Point, b: Point) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(h))


class HaversineRouter:
    def __init__(self, road_factor: float = 1.12) -> None:
        self.road_factor = road_factor

    def distance(self, a: Point, b: Point) -> Distance:
        return Distance(round(haversine_km(a, b) * self.road_factor, 1), "haversine")


class OsrmRouter:
    def __init__(
        self,
        base_url: str,
        fallback: HaversineRouter,
        timeout: float = 3.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.fallback = fallback
        self.client = client or httpx.Client(timeout=timeout)
        self._cache: dict[tuple[Point, Point], Distance] = {}

    def distance(self, a: Point, b: Point) -> Distance:
        key = (a, b)
        if key in self._cache:
            return self._cache[key]
        url = f"{self.base_url}/route/v1/driving/{a[1]},{a[0]};{b[1]},{b[0]}?overview=false"
        try:
            resp = self.client.get(url)
            resp.raise_for_status()
            data = resp.json()
            result = Distance(round(data["routes"][0]["distance"] / 1000, 1), "osrm")
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.warning("OSRM no disponible (%s); se usa estimación haversine", exc)
            est = self.fallback.distance(a, b)
            return Distance(est.km, "haversine (OSRM no disponible)")
        self._cache[key] = result
        return result


def get_router() -> Router:
    settings = get_settings()
    base = HaversineRouter(settings.road_factor)
    if settings.routing_provider == "osrm":
        return OsrmRouter(settings.osrm_url, base, settings.routing_timeout_seconds)
    return base
