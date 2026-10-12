"""Configuración leída desde variables de entorno (prefijo CARGAS_)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET = "dev-only-insecure-secret-change-me-0001"  # noqa: S105


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CARGAS_", env_file=".env", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    # Rol de la aplicación: NO debe ser superusuario ni tener BYPASSRLS (ver docs/arquitectura.md).
    database_url: str = "postgresql+psycopg://cargas:cargas@localhost:5432/cargas"
    secret_key: SecretStr = SecretStr(DEV_SECRET)
    token_ttl_hours: int = 12
    timezone: str = "America/Argentina/Buenos_Aires"

    # Parser: "none" = sólo reglas determinísticas; "ollama" = LLM local con fallback a reglas.
    llm_provider: Literal["none", "ollama"] = "none"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:1.5b"
    llm_timeout_seconds: float = 20.0

    # Distancias: "haversine" (sin red) u "osrm" (servidor OSRM propio) con fallback a haversine.
    routing_provider: Literal["haversine", "osrm"] = "haversine"
    osrm_url: str = "http://localhost:5000"
    routing_timeout_seconds: float = 3.0
    # Factor ruta/línea recta usado por la estimación haversine.
    road_factor: float = Field(default=1.12, ge=1.0, le=2.0)

    # Rechaza la conexión si el rol puede saltear Row-Level Security.
    require_rls_safe_role: bool = True


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    insecure = settings.secret_key.get_secret_value() == DEV_SECRET
    if settings.environment == "production" and insecure:
        raise RuntimeError("CARGAS_SECRET_KEY de desarrollo no permitido en producción")
    return settings


def project_root() -> Path:
    """Directorio con alembic.ini, migrations/ y data/: el repositorio en desarrollo o el
    WORKDIR de la imagen (el paquete instalado vive en site-packages, lejos de esos archivos)."""
    for base in (Path(__file__).resolve().parents[2], Path.cwd()):
        if (base / "alembic.ini").is_file():
            return base
    raise RuntimeError("No se encontró alembic.ini: ejecutá desde la raíz del proyecto")
