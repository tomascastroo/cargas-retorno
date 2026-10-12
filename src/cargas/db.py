"""Conexión a PostgreSQL y contexto de empresa para Row-Level Security.

Cada transacción de negocio fija `app.company_id` con `set_config(..., true)` (alcance de la
transacción). Las políticas RLS de las tablas privadas sólo dejan ver y escribir filas de esa
empresa, aunque una consulta olvide filtrar por `company_id`.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from cargas.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def _session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def set_company(session: Session, company_id: uuid.UUID) -> None:
    session.execute(
        text("SELECT set_config('app.company_id', :cid, true)"), {"cid": str(company_id)}
    )


@contextmanager
def tenant_session(company_id: uuid.UUID) -> Iterator[Session]:
    """Transacción con el contexto de una empresa. Commit al salir, rollback ante error."""
    with _session_factory()() as session, session.begin():
        set_company(session, company_id)
        yield session


@contextmanager
def public_session() -> Iterator[Session]:
    """Transacción sin empresa: sólo ve las tablas compartidas (oportunidades)."""
    with _session_factory()() as session, session.begin():
        yield session


def assert_rls_safe_role() -> None:
    """Falla si el rol de la aplicación puede saltear RLS (superusuario o BYPASSRLS)."""
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
    if row.rolsuper or row.rolbypassrls:
        raise RuntimeError(
            "El rol de base de datos de la aplicación es superusuario o tiene BYPASSRLS: "
            "el aislamiento por empresa no estaría garantizado. Usá un rol sin esos privilegios."
        )
