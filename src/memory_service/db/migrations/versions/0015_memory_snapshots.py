"""memory_snapshots — per-user point-in-time memory backups

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-12

Each row is a self-contained snapshot of one user's memory at a point
in time, stored as a MemoryBundleV1 (the same shape /v1/me/export
produces). The built-in scheduler writes one `auto` snapshot per user
per day (blocks + documents only — the precisely-restorable
primitives); users can also take `manual` snapshots that optionally
include episode content.

Why JSONB in the main DB rather than files: `./deploy.sh backup`
(pg_dump) then covers snapshots automatically — one backup story, no
extra volume to mount. Bundles TOAST-compress well; 30 daily
blocks+documents snapshots per user is tens of MB at most.

Denormalized counts + size_bytes let the list endpoint render without
loading the (potentially large) bundle blob.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "memory_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # "auto" (scheduler) | "manual" (user-triggered).
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column(
            "include_episodes",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        # The MemoryBundleV1 payload.
        sa.Column("bundle", postgresql.JSONB(), nullable=False),
        # Denormalized for cheap listing without loading the bundle.
        sa.Column("blocks_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("documents_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("episodes_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        # Optional human label for manual snapshots.
        sa.Column("note", sa.Text(), nullable=True),
    )
    # Listing is always "this user's snapshots, newest first" + the
    # scheduler's "this user's most recent auto snapshot" probe.
    op.create_index(
        "ix_memory_snapshots_user_created",
        "memory_snapshots",
        ["user_id", "created_at"],
        postgresql_ops={"created_at": "DESC"},
    )
    op.create_index(
        "ix_memory_snapshots_user_kind_created",
        "memory_snapshots",
        ["user_id", "kind", "created_at"],
        postgresql_ops={"created_at": "DESC"},
    )


def downgrade() -> None:
    op.drop_index("ix_memory_snapshots_user_kind_created", table_name="memory_snapshots")
    op.drop_index("ix_memory_snapshots_user_created", table_name="memory_snapshots")
    op.drop_table("memory_snapshots")
