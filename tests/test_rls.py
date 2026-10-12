"""Aislamiento en la base de datos: Row-Level Security, aun sin filtros en la consulta."""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from cargas.db import assert_rls_safe_role, public_session, tenant_session
from cargas.models import AuditEvent, Company, Truck


def make_company(name: str) -> uuid.UUID:
    company_id = uuid.uuid4()
    with tenant_session(company_id) as session:
        session.add(Company(id=company_id, name=name))
        session.flush()
        session.add(
            Truck(
                company_id=company_id,
                plate=f"T{uuid.uuid4().hex[:6]}",
                vehicle_type="semi",
                capacity_pallets=28,
                capacity_kg=30000,
            )
        )
    return company_id


def test_app_role_cannot_bypass_rls() -> None:
    assert_rls_safe_role()  # el rol de test no es superusuario ni tiene BYPASSRLS


def test_queries_without_company_filter_only_see_own_rows() -> None:
    a, b = make_company("A"), make_company("B")
    with tenant_session(b) as session:
        trucks = session.scalars(select(Truck)).all()  # sin WHERE company_id
        companies = session.scalars(select(Company)).all()
    assert {t.company_id for t in trucks} == {b}
    assert [c.id for c in companies] == [b]
    with tenant_session(a) as session:
        assert session.get(Company, b) is None


def test_without_company_context_private_tables_are_empty() -> None:
    make_company("C")
    with public_session() as session:
        assert session.scalars(select(Truck)).all() == []
        assert session.scalar(text("SELECT count(*) FROM users")) == 0


def test_cannot_write_rows_for_another_company() -> None:
    a, b = make_company("D"), make_company("E")
    with pytest.raises(DBAPIError, match="row-level security"), tenant_session(a) as session:
        session.add(
            Truck(
                company_id=b,
                plate="INTRUSO",
                vehicle_type="semi",
                capacity_pallets=1,
                capacity_kg=1000,
            )
        )
        session.flush()


def test_cannot_update_rows_of_another_company() -> None:
    a, b = make_company("F"), make_company("G")
    with tenant_session(a) as session:
        result = session.execute(
            text("UPDATE trucks SET active = false WHERE company_id = :b"), {"b": b}
        )
        assert result.rowcount == 0


def test_audit_events_are_append_only() -> None:
    a = make_company("H")
    with tenant_session(a) as session:
        session.add(AuditEvent(company_id=a, action="x", entity_type="t", data={}))
    with pytest.raises(DBAPIError, match="sólo inserción"), tenant_session(a) as session:
        session.execute(text("UPDATE audit_events SET action = 'y'"))
    with pytest.raises(DBAPIError, match="sólo inserción"), tenant_session(a) as session:
        session.execute(text("DELETE FROM audit_events"))
