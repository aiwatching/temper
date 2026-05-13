"""add episode_metadata.extraction_status + extraction_error

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing rows are all from the synchronous path, so they're "done."
    op.add_column(
        "episode_metadata",
        sa.Column(
            "extraction_status",
            sa.String(length=16),
            nullable=False,
            server_default="done",
        ),
    )
    op.add_column(
        "episode_metadata",
        sa.Column("extraction_error", sa.String(length=2048), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("episode_metadata", "extraction_error")
    op.drop_column("episode_metadata", "extraction_status")
