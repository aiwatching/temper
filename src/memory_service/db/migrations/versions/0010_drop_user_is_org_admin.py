"""drop users.is_org_admin — org_admin role removed; only super_admin remains

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-13

The product collapsed to a single privileged role (super_admin). The
per-user org_admin flag was the only thing reading this column and is
gone. Drop it. SQLite needs batch_alter_table for DROP COLUMN.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("is_org_admin")


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_org_admin",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
