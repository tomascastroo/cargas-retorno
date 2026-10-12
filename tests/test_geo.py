import httpx

from cargas.geo.places import find_places, normalize, resolve_place
from cargas.geo.routing import HaversineRouter, OsrmRouter

CBA, BA = (-31.4201, -64.1888), (-34.6037, -58.3816)


def test_aliases_and_accents() -> None:
    for alias in ("Bs As", "CABA", "capital federal", "buenos aires"):
        assert resolve_place(alias).name == "Buenos Aires"
    assert resolve_place("cordoba").name == "Córdoba"
    assert resolve_place("Atlantis") is None


def test_longest_alias_wins() -> None:
    names = [p.name for _, p in find_places(normalize("de Villa María a Santa Fe"))]
    assert names == ["Villa María", "Santa Fe"]


def test_haversine_estimate_is_close_to_road_distance() -> None:
    km = HaversineRouter(1.12).distance(CBA, BA).km
    assert 650 < km < 780  # por ruta son ~700 km


def test_osrm_used_when_available() -> None:
    transport = httpx.MockTransport(
        lambda _r: httpx.Response(200, json={"routes": [{"distance": 702_300}]})
    )
    router = OsrmRouter(
        "http://osrm.test", HaversineRouter(), client=httpx.Client(transport=transport)
    )
    d = router.distance(CBA, BA)
    assert (d.km, d.method) == (702.3, "osrm")


def test_osrm_failure_falls_back_to_haversine() -> None:
    def down(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    router = OsrmRouter(
        "http://osrm.test",
        HaversineRouter(),
        client=httpx.Client(transport=httpx.MockTransport(down)),
    )
    d = router.distance(CBA, BA)
    assert d.method == "haversine (OSRM no disponible)"
    assert d.km > 600
