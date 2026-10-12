"""Row-Level Security por empresa y auditoría inmutable

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-12

Cada tabla privada sólo expone las filas cuyo company_id coincide con el valor de la variable
de sesión `app.company_id` (fijada por la aplicación al iniciar cada transacción). FORCE ROW
LEVEL SECURITY hace que la regla aplique también al dueño de las tablas. Un superusuario o un
rol con BYPASSRLS la saltean: la aplicación verifica al iniciar que su rol no tenga esos
privilegios (cargas.db.assert_rls_safe_role).
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_COMPANY = "NULLIF(current_setting('app.company_id', true), '')::uuid"
TENANT_TABLES = (
    "users",
    "agents",
    "trucks",
    "conversations",
    "messages",
    "trips",
    "matches",
    "negotiations",
    "bookings",
    "audit_events",
)


def upgrade() -> None:
    op.execute("ALTER TABLE companies ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE companies FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON companies "
        f"USING (id = {CURRENT_COMPANY}) WITH CHECK (id = {CURRENT_COMPANY})"
    )
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (company_id = {CURRENT_COMPANY}) WITH CHECK (company_id = {CURRENT_COMPANY})"
        )
    # Login: antes de conocer la empresa hay que buscar al usuario por email. Sólo lectura y
    # sólo dentro de una transacción que active explícitamente app.auth_lookup.
    op.execute(
        "CREATE POLICY auth_lookup ON users FOR SELECT "
        "USING (current_setting('app.auth_lookup', true) = 'on')"
    )

    op.execute(
        """
        CREATE FUNCTION audit_events_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events es de sólo inserción';
        END $$;
        """
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_change BEFORE UPDATE OR DELETE ON audit_events "
        "FOR EACH ROW EXECUTE FUNCTION audit_events_immutable()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_events_no_change ON audit_events")
    op.execute("DROP FUNCTION audit_events_immutable()")
    op.execute("DROP POLICY auth_lookup ON users")
    for table in (*TENANT_TABLES, "companies"):
        op.execute(f"DROP POLICY tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
