"""Conectores de fuentes de cargas, intercambiables.

Todos devuelven registros crudos (dict) con el esquema de `RawLoad` (ver normalize.py). Cada
fuente decide cómo obtenerlos; la normalización, deduplicación y vencimiento son comunes.

- `JsonFileConnector`: archivo JSON local (datos de demostración).
- `CsvFileConnector`: planilla CSV (p. ej. lo que comparte una cooperativa o un dador directo).
- `HttpJsonConnector`: API JSON de un socio que autorice el uso de sus datos. Desactivado por
  defecto; no hay en el MVP ninguna fuente pública gratuita integrada (ver docs/fuentes.md).

Los archivos demo usan fechas relativas (`pickup_in_days`, `valid_hours`, `seen_hours_ago`)
para que la demostración funcione cualquier día; `resolve_relative_dates` las convierte en
fechas absolutas antes de normalizar, como las entregaría una fuente real.
"""

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx


class SourceError(Exception):
    """La fuente no pudo consultarse (red, formato, permisos)."""


class Connector(Protocol):
    def fetch(self, now: datetime) -> list[dict[str, Any]]: ...


def resolve_relative_dates(record: dict[str, Any], now: datetime) -> dict[str, Any]:
    rec = dict(record)
    today = now.date()
    if "pickup_in_days" in rec:
        start = today + timedelta(days=int(rec.pop("pickup_in_days")))
        window = int(rec.pop("pickup_window_days", 0) or 0)
        rec.setdefault("pickup_from", start.isoformat())
        rec.setdefault("pickup_to", (start + timedelta(days=window)).isoformat())
    if "valid_hours" in rec:
        rec.setdefault(
            "valid_until", (now + timedelta(hours=float(rec.pop("valid_hours")))).isoformat()
        )
    if "seen_hours_ago" in rec:
        rec.setdefault(
            "published_at", (now - timedelta(hours=float(rec.pop("seen_hours_ago")))).isoformat()
        )
    return rec


class JsonFileConnector:
    def __init__(self, path: Path) -> None:
        self.path = path

    def fetch(self, now: datetime) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SourceError(f"no se pudo leer {self.path.name}: {exc}") from exc
        if not isinstance(data, list):
            raise SourceError("se esperaba una lista de cargas")
        return [resolve_relative_dates(r, now) for r in data]


_LIST_COLUMNS = ("vehicle_types", "body_types")


class CsvFileConnector:
    def __init__(self, path: Path) -> None:
        self.path = path

    def fetch(self, now: datetime) -> list[dict[str, Any]]:
        try:
            with self.path.open(encoding="utf-8", newline="") as fh:
                rows = list(csv.DictReader(fh))
        except OSError as exc:
            raise SourceError(f"no se pudo leer {self.path.name}: {exc}") from exc
        records = []
        for row in rows:
            rec: dict[str, Any] = {k: v for k, v in row.items() if v not in (None, "")}
            for col in _LIST_COLUMNS:
                if col in rec:
                    rec[col] = [v.strip() for v in rec[col].split("|") if v.strip()]
            records.append(resolve_relative_dates(rec, now))
        return records


class HttpJsonConnector:
    def __init__(self, url: str, timeout: float = 10.0, client: httpx.Client | None = None) -> None:
        self.url = url
        self.client = client or httpx.Client(timeout=timeout)

    def fetch(self, now: datetime) -> list[dict[str, Any]]:
        try:
            resp = self.client.get(self.url)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(f"{type(exc).__name__}: {exc}") from exc
        if not isinstance(data, list):
            raise SourceError("se esperaba una lista de cargas")
        return data
