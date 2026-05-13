"""add users.is_org_admin

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite needs the server_default during ADD COLUMN because existing rows
    # would otherwise hold NULL on a NOT NULL column. Postgres handles either
    # but using server_default keeps a single migration that works for both.
    op.add_column(
        "users",
        sa.Column(
            "is_org_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "is_org_admin")
