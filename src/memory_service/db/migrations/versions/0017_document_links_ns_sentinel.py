"""document_links.target_namespace: NULL → "" sentinel + NOT NULL

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-22

Postgres primary keys cannot contain NULL — and document_links's PK is
(source_document_id, target_path, target_namespace) with target_namespace
declared nullable in 0013. Any wikilink that resolves to the source's
own namespace (the common case: `[[other/page]]`) had target_namespace
set to NULL → INSERT failed → the whole document PUT returned 500.

In practice this killed every PUT that contained ANY in-namespace
wikilink. Caught while migrating a chat summary archive — the doc had
a `[[chats/<other-chat>]]` reference and the upsert 500'd.

Fix: rewrite existing NULLs to "" and tighten the column to NOT NULL.
The application's wikilink emitter passes "" for same-namespace links
(see core/documents._refresh_links). "" is a legitimate sentinel here
because no real namespace string is empty.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE document_links SET target_namespace = '' WHERE target_namespace IS NULL"
    )
    with op.batch_alter_table("document_links") as batch_op:
        batch_op.alter_column(
            "target_namespace",
            existing_type=sa.String(length=255),
            nullable=False,
            server_default="",
        )


def downgrade() -> None:
    with op.batch_alter_table("document_links") as batch_op:
        batch_op.alter_column(
            "target_namespace",
            existing_type=sa.String(length=255),
            nullable=True,
            server_default=None,
        )
    op.execute(
        "UPDATE document_links SET target_namespace = NULL WHERE target_namespace = ''"
    )
