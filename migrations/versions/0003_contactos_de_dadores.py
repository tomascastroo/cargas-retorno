"""Agenda privada de dadores de carga por empresa

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_COMPANY = "NULLIF(current_setting('app.company_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "shipper_contacts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("phone", sa.String(length=20), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("cities", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("cargo_types", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shipper_contacts_company_id", "shipper_contacts", ["company_id"])
    # Un mismo teléfono / email no se repite dentro de una empresa (sí entre empresas).
    op.create_index(
        "uq_shipper_contacts_phone",
        "shipper_contacts",
        ["company_id", "phone"],
        unique=True,
        postgresql_where=sa.text("phone IS NOT NULL"),
    )
    op.create_index(
        "uq_shipper_contacts_email",
        "shipper_contacts",
        ["company_id", "email"],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
    )
    op.execute("ALTER TABLE shipper_contacts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE shipper_contacts FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON shipper_contacts "
        f"USING (company_id = {CURRENT_COMPANY}) WITH CHECK (company_id = {CURRENT_COMPANY})"
    )


def downgrade() -> None:
    op.drop_table("shipper_contacts")
