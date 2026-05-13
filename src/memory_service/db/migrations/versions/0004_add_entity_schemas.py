"""add entity_schemas

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "entity_schemas",
        sa.Column("namespace", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=1024), nullable=True),
        sa.Column("fields_json", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace", "name", name="uq_entity_schema_ns_name"),
    )
    op.create_index(
        op.f("ix_entity_schemas_namespace"),
        "entity_schemas",
        ["namespace"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_entity_schemas_namespace"), table_name="entity_schemas")
    op.drop_table("entity_schemas")
