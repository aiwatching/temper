"""api_keys: drop the (user_id, agent_slug) unique constraint

Revision ID: 0016
Revises: 0015
Create Date: 2026-06-12

Multiple API keys pointing at the SAME agent_slug is a legitimate,
intended pattern — that's how you give several agents / machines /
sessions their own credential while sharing one memory namespace
(agent:<user_id>/<slug>). Revoking one key then doesn't cut off the
others.

The model + endpoint copy already described same-slug keys as "explicit
memory sharing", but uq_api_keys_user_agent_slug (added in 0011)
contradicted that by rejecting the second key with a 409. Drop it.

Key identity / auth is unaffected: lookups go through the unique
key_hash, never the slug.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.drop_constraint("uq_api_keys_user_agent_slug", type_="unique")


def downgrade() -> None:
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.create_unique_constraint(
            "uq_api_keys_user_agent_slug", ["user_id", "agent_slug"]
        )
