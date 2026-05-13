"""episode_metadata.graphiti_episode_id

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-13

Separates "our row's PK" (a tracking UUID we generate up front so the
API can return immediately on async writes) from "Graphiti's episode
UUID" (the actual node id in FalkorDB). For sync writes the two are
identical; for async writes graphiti_episode_id starts NULL and gets
populated when extraction completes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "episode_metadata",
        sa.Column("graphiti_episode_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        op.f("ix_episode_metadata_graphiti_episode_id"),
        "episode_metadata",
        ["graphiti_episode_id"],
        unique=False,
    )
    # Pre-existing rows are all sync writes, so id == graphiti's uuid.
    op.execute("UPDATE episode_metadata SET graphiti_episode_id = id")


def downgrade() -> None:
    op.drop_index(
        op.f("ix_episode_metadata_graphiti_episode_id"),
        table_name="episode_metadata",
    )
    op.drop_column("episode_metadata", "graphiti_episode_id")
