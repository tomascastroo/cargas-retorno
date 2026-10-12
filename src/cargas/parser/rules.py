"""Parser determinístico: reglas y expresiones regulares sobre español rioplatense.

Funciona sin red ni modelos. Cubre los mensajes típicos ("vuelvo mañana de Córdoba a Buenos
Aires vacío", "salgo el viernes de Rosario para Mendoza con un semi de 28 pallets").
"""

import re
from datetime import date, timedelta

from cargas.geo.places import find_places, normalize
from cargas.parser.schemas import TripDraft

_ORIGIN_MARKERS = ("desde", "de", "en", "saliendo de", "salgo de", "vuelvo de", "estoy en")
_DEST_MARKERS = ("a", "para", "hasta", "hacia", "rumbo a", "destino", "con destino a", "vuelvo a")

_WEEKDAYS = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "domingo": 6,
}

_VEHICLES = [
    (r"semi(rremolque)?s?|semi ?remolque|acoplado|bitren", "semi"),
    (r"balancin(es)?", "balancin"),
    (r"chasis", "chasis"),
    (r"camioneta|utilitario|furgon", "camioneta"),
]


def _role_for(prefix: str) -> str | None:
    """Mira las últimas palabras antes de una localidad: ¿es origen o destino?"""
    words = prefix.split()[-3:]
    tail = " ".join(words)
    for n in (3, 2, 1):
        chunk = " ".join(words[-n:]) if len(words) >= n else None
        if not chunk:
            continue
        if chunk in _DEST_MARKERS:
            return "destination"
        if chunk in _ORIGIN_MARKERS:
            return "origin"
    if tail.endswith((" al", " a la")) or tail == "al":
        return "destination"
    return None


def _parse_places(text: str) -> tuple[str | None, str | None]:
    places = find_places(text)
    origin = destination = None
    unassigned = []
    for pos, place in places:
        role = _role_for(re.sub(r"[^\w\s]", " ", text[:pos]))
        if role == "origin" and origin is None:
            origin = place.name
        elif role == "destination" and destination is None:
            destination = place.name
        else:
            unassigned.append(place.name)
    # Sin marcadores claros: la primera mencionada es el origen y la siguiente el destino.
    for name in unassigned:
        if origin is None and name != destination:
            origin = name
        elif destination is None and name != origin:
            destination = name
    return origin, destination


def _parse_date(text: str, today: date) -> date | None:
    if re.search(r"pasado ?manana", text):
        return today + timedelta(days=2)
    # "mañana" como día (no "a la mañana" / "por la mañana", que son franjas horarias).
    if re.search(r"(?<!la )\bmanana\b", text):
        return today + timedelta(days=1)
    if re.search(r"\bhoy\b|\bahora\b|\besta (tarde|noche)\b", text):
        return today
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else today.year
        if year < 100:
            year += 2000
        try:
            parsed = date(year, month, day)
        except ValueError:
            return None
        if not m.group(3) and parsed < today:
            parsed = parsed.replace(year=today.year + 1)
        return parsed
    for name, weekday in _WEEKDAYS.items():
        if re.search(rf"\b{name}\b", text):
            delta = (weekday - today.weekday()) % 7
            return today + timedelta(days=delta or 7)
    m = re.search(r"\bel (\d{1,2})\b(?! pallets| palets| tn| toneladas| kg)", text)
    if m:
        day = int(m.group(1))
        candidate_month, candidate_year = today.month, today.year
        if day < today.day:
            candidate_month += 1
            if candidate_month == 13:
                candidate_month, candidate_year = 1, candidate_year + 1
        try:
            return date(candidate_year, candidate_month, day)
        except ValueError:
            return None
    return None


def _parse_number(raw: str) -> float:
    return float(raw.replace(".", "").replace(",", ".")) if "," in raw else float(raw)


def parse_trip(message: str, today: date) -> TripDraft:
    text = normalize(message)
    origin, destination = _parse_places(text)

    vehicle = None
    for pattern, vtype in _VEHICLES:
        if re.search(rf"\b({pattern})\b", text):
            vehicle = vtype
            break

    refrigerated = True if re.search(r"refrigerad|termic|frigorific|frio\b", text) else None

    pallets = None
    m = re.search(r"\b(\d{1,2})\s*(pallets?|palets?|posiciones)\b", text)
    if m:
        pallets = int(m.group(1))

    weight = None
    m = re.search(r"\b(\d+(?:[.,]\d+)?)\s*(tn|t|toneladas|tons?)\b", text)
    if m:
        weight = int(_parse_number(m.group(1)) * 1000)
    else:
        m = re.search(r"\b(\d{3,6})\s*kg\b", text)
        if m:
            weight = int(m.group(1))

    plate = None
    # Patentes Mercosur (AB123CD) o anteriores (ABC123), escritas sin espacios.
    m = re.search(r"\b([a-z]{2}\d{3}[a-z]{2}|[a-z]{3}\d{3})\b", text)
    if m:
        plate = m.group(1).upper()

    empty = True if re.search(r"\bvaci[oa]s?\b|sin carga", text) else None

    return TripDraft(
        origin=origin,
        destination=destination,
        departure_date=_parse_date(text, today),
        vehicle_type=vehicle,
        refrigerated=refrigerated,
        pallets=pallets if pallets and 1 <= pallets <= 60 else None,
        weight_kg=weight if weight and 100 <= weight <= 60_000 else None,
        plate=plate,
        empty=empty,
    )
