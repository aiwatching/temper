"""memory_blocks — structured key/value memory for first-person assertions

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-15

Adds a `memory_blocks` table that holds JSONB key/value records scoped
per (user, agent). Designed for the "user directly asserts something
durable about themselves" class of memory (nickname, preferences,
current focus, daily routine) — which Graphiti's entity/edge
extraction is structurally bad at (pronouns filtered, no self-entity,
agency flips, append-only summaries).

Schema choices:
- `agent_slug` defaults to '*' (sentinel for "global, all agents").
  Postgres NULL semantics would let multiple NULL rows per
  (user, key) slip past the UNIQUE; the sentinel sidesteps that.
- `block_value` is JSONB so each caller decides the shape; the
  service treats it as opaque.
- `pinned=true` is the signal for "always include in the system
  prompt for every turn" — Smith's before_agent_start hook reads
  these and injects them.
- `priority` only matters for ordering pinned blocks in the prompt.
- `description` is shown to the agent on read so it self-documents.
- `updated_by` is informational (e.g. "agent:smith", "user:admin-ui");
  not enforced.

Indices: one partial on (user, agent) WHERE pinned=true (the hot path
— Smith fetches these every turn), and one general on
(user, agent) for prefix / scope listings.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "memory_blocks",
        # NOTE: ids are VARCHAR(36) (string-encoded UUIDs) throughout
        # TEMPER's schema — see UUIDPKMixin / migration 0001's users
        # table. Don't switch to native postgresql.UUID here without
        # also migrating every other id column; the FK to users.id
        # would mismatch and Postgres refuses the constraint.
        sa.Column(
            "id",
            sa.String(length=36),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()::text"),
        ),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # '*' is the global-scope sentinel. NOT NULL avoids the
        # Postgres "NULL != NULL" quirk that would defeat UNIQUE.
        sa.Column(
            "agent_slug",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'*'"),
        ),
        sa.Column("block_key", sa.String(length=255), nullable=False),
        sa.Column("block_value", postgresql.JSONB(), nullable=False),
        sa.Column(
            "pinned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "priority",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.String(length=128), nullable=True),
        sa.UniqueConstraint(
            "user_id", "agent_slug", "block_key",
            name="uq_memory_blocks_user_agent_key",
        ),
    )
    # Partial index on the hot read path — Smith pulls all pinned
    # blocks per turn for the system prompt.
    op.create_index(
        "ix_memory_blocks_user_pinned",
        "memory_blocks",
        ["user_id", "agent_slug"],
        postgresql_where=sa.text("pinned = true"),
    )
    op.create_index(
        "ix_memory_blocks_user_agent",
        "memory_blocks",
        ["user_id", "agent_slug"],
    )


def downgrade() -> None:
    op.drop_index("ix_memory_blocks_user_agent", table_name="memory_blocks")
    op.drop_index("ix_memory_blocks_user_pinned", table_name="memory_blocks")
    op.drop_table("memory_blocks")
