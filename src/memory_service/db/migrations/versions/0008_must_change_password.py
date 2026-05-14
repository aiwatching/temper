"""users.must_change_password

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-14

Flag set on accounts that were created with a default / temporary
password (default-admin bootstrap, future "admin reset password"
shortcut). The user is allowed to log in but is forced through the
change-password screen before they can use anything else.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
