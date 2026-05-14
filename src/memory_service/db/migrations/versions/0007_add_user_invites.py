"""users.invite_token + nullable password_hash

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-14

Introduces the invite-only onboarding flow for enterprise deploys:

  - `invite_token` / `invite_token_expires_at` — created by admin when
    they add a user; the new user clicks a link bearing this token to
    set their own password.
  - `password_hash` becomes nullable (the row exists during the invite
    window before any password has been chosen).
  - `invited_by_user_id` — audit trail of who created this account.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite needs batch_alter_table to alter NOT NULL on a column or
    # to add a self-referencing FK (it rebuilds the table). Postgres
    # treats batch ops as regular ones.
    #
    # The self-ref FK for invited_by_user_id is declared on the ORM
    # model but we skip creating the DB-level constraint here — adding
    # a self-ref FK in SQLite under batch mode trips alembic's topo
    # sort. Postgres deployments running `create_all` once before
    # baseline get the constraint via the model anyway; this column
    # is audit-only, so missing FK enforcement is acceptable.
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("invite_token", sa.String(length=64), nullable=True),
        )
        batch_op.add_column(
            sa.Column("invite_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        )
        batch_op.add_column(
            sa.Column("invited_by_user_id", sa.String(length=36), nullable=True),
        )
        batch_op.alter_column(
            "password_hash",
            existing_type=sa.String(length=255),
            nullable=True,
        )
    # Index outside the batch — sqlite has no problem with a separate
    # CREATE INDEX after the table rebuild.
    op.create_index(
        op.f("ix_users_invite_token"),
        "users",
        ["invite_token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_users_invite_token"), table_name="users")
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "password_hash",
            existing_type=sa.String(length=255),
            nullable=False,
        )
        batch_op.drop_column("invited_by_user_id")
        batch_op.drop_column("invite_token_expires_at")
        batch_op.drop_column("invite_token")
