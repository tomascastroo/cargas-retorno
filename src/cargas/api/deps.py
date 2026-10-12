"""Autenticación y contexto de empresa por request.

Cada endpoint privado abre su transacción con `company_context(claims)`: fija el company_id del
token para Row-Level Security y carga al usuario. Además, todas las consultas filtran por
company_id explícitamente (doble barrera: aplicación + base de datos).
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from cargas.auth import TokenClaims, decode_token
from cargas.config import get_settings
from cargas.db import tenant_session
from cargas.models import User

_bearer = HTTPBearer(auto_error=False)


def current_claims(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),  # noqa: B008
) -> TokenClaims:
    claims = decode_token(creds.credentials) if creds else None
    if claims is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido o vencido")
    return claims


@contextmanager
def company_context(claims: TokenClaims) -> Iterator[tuple[Session, User]]:
    with tenant_session(claims.company_id) as session:
        user = session.get(User, claims.user_id)
        if user is None or user.company_id != claims.company_id:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuario inexistente")
        yield session, user


def not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "No encontrado")


def owned[T](session: Session, model: type[T], obj_id: uuid.UUID, company_id: uuid.UUID) -> T:
    """Obtiene una entidad privada sólo si pertenece a la empresa; si no, 404 (no 403, para no
    revelar que existe)."""
    obj = session.get(model, obj_id)
    if obj is None or getattr(obj, "company_id", None) != company_id:
        raise not_found()
    return obj


def local_now() -> tuple[datetime, date]:
    now = datetime.now(ZoneInfo(get_settings().timezone))
    return now, now.date()
