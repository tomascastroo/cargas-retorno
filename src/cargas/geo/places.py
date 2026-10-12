"""Nomenclátor local de localidades argentinas (coordenadas aproximadas del centro urbano,
tomadas de OpenStreetMap). Evita depender de un geocodificador externo en el MVP.

Para ampliar: agregar filas a PLACES. Un geocodificador propio (Nominatim con el extracto de
Argentina de Geofabrik) puede reemplazar esta tabla sin cambiar las interfaces.
"""

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Place:
    name: str
    province: str
    lat: float
    lon: float


# nombre, provincia, lat, lon, alias adicionales
_RAW: list[tuple[str, str, float, float, tuple[str, ...]]] = [
    (
        "Buenos Aires",
        "CABA",
        -34.6037,
        -58.3816,
        ("bs as", "bsas", "bs. as.", "caba", "capital federal", "capital", "baires"),
    ),
    ("La Plata", "Buenos Aires", -34.9205, -57.9536, ()),
    ("Mar del Plata", "Buenos Aires", -38.0055, -57.5426, ("mdq",)),
    ("Bahía Blanca", "Buenos Aires", -38.7196, -62.2724, ()),
    ("Pergamino", "Buenos Aires", -33.8900, -60.5736, ()),
    ("Junín", "Buenos Aires", -34.5856, -60.9589, ()),
    ("San Nicolás", "Buenos Aires", -33.3342, -60.2108, ()),
    ("Zárate", "Buenos Aires", -34.0981, -59.0286, ()),
    ("Campana", "Buenos Aires", -34.1633, -58.9592, ()),
    ("Pilar", "Buenos Aires", -34.4587, -58.9142, ()),
    ("Escobar", "Buenos Aires", -34.3483, -58.7959, ()),
    ("Luján", "Buenos Aires", -34.5703, -59.1050, ()),
    ("Mercedes", "Buenos Aires", -34.6515, -59.4307, ()),
    ("Olavarría", "Buenos Aires", -36.8927, -60.3225, ()),
    ("Tandil", "Buenos Aires", -37.3217, -59.1332, ()),
    ("Córdoba", "Córdoba", -31.4201, -64.1888, ("cba", "cordoba capital")),
    ("Villa María", "Córdoba", -32.4075, -63.2402, ()),
    ("Río Cuarto", "Córdoba", -33.1232, -64.3493, ()),
    ("San Francisco", "Córdoba", -31.4280, -62.0827, ()),
    ("Marcos Juárez", "Córdoba", -32.6976, -62.1066, ()),
    ("Bell Ville", "Córdoba", -32.6259, -62.6888, ()),
    ("Villa Carlos Paz", "Córdoba", -31.4241, -64.4978, ("carlos paz",)),
    ("Jesús María", "Córdoba", -30.9815, -64.0943, ()),
    ("Rosario", "Santa Fe", -32.9442, -60.6505, ()),
    ("Santa Fe", "Santa Fe", -31.6333, -60.7000, ()),
    ("Rafaela", "Santa Fe", -31.2503, -61.4867, ()),
    ("Venado Tuerto", "Santa Fe", -33.7456, -61.9688, ()),
    ("Paraná", "Entre Ríos", -31.7333, -60.5297, ()),
    ("Concordia", "Entre Ríos", -31.3929, -58.0209, ()),
    ("Gualeguaychú", "Entre Ríos", -33.0103, -58.5172, ()),
    ("Mendoza", "Mendoza", -32.8895, -68.8458, ()),
    ("San Juan", "San Juan", -31.5375, -68.5364, ()),
    ("San Luis", "San Luis", -33.3017, -66.3378, ()),
    ("Villa Mercedes", "San Luis", -33.6757, -65.4578, ()),
    ("Santa Rosa", "La Pampa", -36.6167, -64.2833, ()),
    ("San Miguel de Tucumán", "Tucumán", -26.8083, -65.2176, ("tucuman",)),
    ("Salta", "Salta", -24.7821, -65.4232, ()),
    ("San Salvador de Jujuy", "Jujuy", -24.1858, -65.2995, ("jujuy",)),
    ("Santiago del Estero", "Santiago del Estero", -27.7951, -64.2615, ("santiago",)),
    ("San Fernando del Valle de Catamarca", "Catamarca", -28.4696, -65.7795, ("catamarca",)),
    ("La Rioja", "La Rioja", -29.4131, -66.8558, ()),
    ("Resistencia", "Chaco", -27.4606, -58.9839, ()),
    ("Corrientes", "Corrientes", -27.4692, -58.8306, ()),
    ("Posadas", "Misiones", -27.3621, -55.9009, ()),
    ("Neuquén", "Neuquén", -38.9516, -68.0591, ()),
    ("Viedma", "Río Negro", -40.8135, -62.9967, ()),
    ("Trelew", "Chubut", -43.2489, -65.3051, ()),
    ("Comodoro Rivadavia", "Chubut", -45.8641, -67.4966, ("comodoro",)),
]


def normalize(text: str) -> str:
    """Minúsculas, sin tildes y con espacios simples."""
    decomposed = unicodedata.normalize("NFKD", text)
    no_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", no_accents.lower()).strip()


PLACES: dict[str, Place] = {}
_ALIASES: dict[str, Place] = {}
for _name, _prov, _lat, _lon, _aliases in _RAW:
    _place = Place(_name, _prov, _lat, _lon)
    PLACES[normalize(_name)] = _place
    for _alias in (_name, *_aliases):
        _ALIASES[normalize(_alias)] = _place

# Alias más largos primero: "villa maria" antes que "maria", "santa fe" antes que "fe".
_ALIAS_PATTERN = re.compile(
    r"(?<![a-z])("
    + "|".join(re.escape(a) for a in sorted(_ALIASES, key=len, reverse=True))
    + r")(?![a-z])"
)


def resolve_place(name: str) -> Place | None:
    """Nombre o alias exacto (sin importar tildes/mayúsculas) → localidad."""
    return _ALIASES.get(normalize(name).rstrip("."))


def find_places(text: str) -> list[tuple[int, Place]]:
    """Localidades mencionadas en un texto normalizado, con su posición, en orden de aparición."""
    found: list[tuple[int, Place]] = []
    for m in _ALIAS_PATTERN.finditer(text):
        found.append((m.start(), _ALIASES[m.group(1)]))
    return found
