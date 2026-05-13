"""add episode_metadata

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-12 22:16:30.218902

Was empty in the original v0.5 commit because the dev DB always got
bootstrapped via Base.metadata.create_all() in conftest + lifespan,
so nobody noticed the migration didn't actually create the table.
A real prod deploy that boots from `alembic upgrade head` on an empty
Postgres won't have that fallback. Backfilled to do its job.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "episode_metadata",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("namespace", sa.String(length=128), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_by_agent", sa.String(length=128), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("reference_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_episode_metadata_namespace"),
        "episode_metadata",
        ["namespace"],
        unique=False,
    )
    op.create_index(
        op.f("ix_episode_metadata_created_by_user_id"),
        "episode_metadata",
        ["created_by_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_episode_metadata_created_by_user_id"),
        table_name="episode_metadata",
    )
    op.drop_index(
        op.f("ix_episode_metadata_namespace"),
        table_name="episode_metadata",
    )
    op.drop_table("episode_metadata")
