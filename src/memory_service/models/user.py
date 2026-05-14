"""User account + role flags + invite state.

Roles:
  - `is_super_admin`: full system access; can create orgs, write to
    `public`, manage any user. Only role that can edit org/group state.
  - is_active=False blocks login but keeps data.

Invite lifecycle:
  - Admin creates a user → sets `invite_token` + `invite_token_expires_at`,
    leaves `password_hash` NULL.
  - User visits `/admin/accept-invite?token=…` → sets password →
    `password_hash` populated, invite_token cleared.
  - Tokens are single-use and expire (24h by default).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from memory_service.models._base import Base, TimestampMixin, UUIDPKMixin


class User(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    # Optional short login handle (e.g. "admin"). Email stays the record
    # key; username is purely an alias for the login form.
    username: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, default=None
    )
    # Nullable: invited users have no password until they accept the invite.
    password_hash: Mapped[str | None] = mapped_column(String(255), default=None)
    display_name: Mapped[str | None] = mapped_column(String(255), default=None)
    org_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="SET NULL"), default=None, index=True
    )
    is_super_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Set when the account ships with a default / reset password — the
    # user can log in but is force-routed to the change-password screen.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)

    # Invite flow. Populated when admin creates the user; cleared when
    # the user accepts the invite. Token is the random URL-safe string
    # the user pastes back; expires_at lets us refuse stale links.
    invite_token: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, default=None
    )
    invite_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    invited_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
