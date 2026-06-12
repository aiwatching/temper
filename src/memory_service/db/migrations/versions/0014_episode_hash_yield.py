"""episode_metadata: content hash for write-dedup + extraction yield counts

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-12

Three nullable columns on episode_metadata:

- `content_sha256` — hex digest of the episode body as submitted.
  Powers the 24h write-dedup guard: same (namespace, hash) within
  the window → the write is acknowledged but not re-extracted.
  Nullable because pre-0014 rows never recorded it (they simply
  never participate in dedup matching).

- `extracted_entities_count` / `extracted_facts_count` — how many
  nodes / edges Graphiti yielded for this episode, recorded at write
  time. Lets /v1/stats report "zero-yield" episodes (extraction ran
  fine but produced nothing) without an expensive per-episode graph
  query. NULL = unknown (pre-0014 row or extraction still pending).

Index on (namespace, content_sha256) serves the dedup lookup; the
extra created_at term isn't needed — the window filter is cheap once
the hash narrows the candidates to a handful.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("episode_metadata") as batch_op:
        batch_op.add_column(
            sa.Column("content_sha256", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("extracted_entities_count", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("extracted_facts_count", sa.Integer(), nullable=True)
        )
        batch_op.create_index(
            "ix_episode_metadata_ns_hash",
            ["namespace", "content_sha256"],
        )


def downgrade() -> None:
    with op.batch_alter_table("episode_metadata") as batch_op:
        batch_op.drop_index("ix_episode_metadata_ns_hash")
        batch_op.drop_column("extracted_facts_count")
        batch_op.drop_column("extracted_entities_count")
        batch_op.drop_column("content_sha256")
