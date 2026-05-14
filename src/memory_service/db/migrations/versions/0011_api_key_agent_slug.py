"""api_keys.agent_slug — per-agent sub-namespace under user:<id>

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-13

Adds a nullable `agent_slug` to API keys. When set, requests authed by
that key default to namespace `agent:<user_id>/<slug>` instead of the
flat `user:<user_id>` — so two agents under one user no longer share
memory unless they're deliberately created with the same slug.

NULL is legacy / unscoped: existing keys keep their original behavior.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.add_column(
            sa.Column("agent_slug", sa.String(length=64), nullable=True)
        )
        # One agent_slug per user. NULL is allowed any number of times so
        # legacy keys (and explicitly-unscoped keys) can coexist.
        batch_op.create_unique_constraint(
            "uq_api_keys_user_agent_slug", ["user_id", "agent_slug"]
        )


def downgrade() -> None:
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.drop_constraint("uq_api_keys_user_agent_slug", type_="unique")
        batch_op.drop_column("agent_slug")
