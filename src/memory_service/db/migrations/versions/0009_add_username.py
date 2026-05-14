"""users.username — optional login handle

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-14

Adds a `username` column so the default admin (and anyone you want)
can sign in by a short handle instead of typing a full email. Email
remains the primary record key — username is purely a login alias.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("username", sa.String(length=64), nullable=True)
    )
    op.create_index(
        op.f("ix_users_username"), "users", ["username"], unique=True
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_column("users", "username")
