"""Schemas for admin-managed user CRUD + invite flow.

Separate from `schemas/auth.py` (which is about the authenticated
session) — these shapes are what admins POST when adding/managing
other users on the system.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class CreateUserRequest(BaseModel):
    """Admin creates a user. They land in invite state — no password
    set, an invite_token returned, user clicks the URL to set their own
    password."""

    email: EmailStr
    # Optional short login handle. Email stays the record key; if you
    # set username, the user can sign in with either.
    username: str | None = Field(default=None, min_length=1, max_length=64)
    display_name: str | None = Field(default=None, max_length=255)
    org_slug: str | None = None
    is_super_admin: bool = False
    group_slugs: list[str] = Field(default_factory=list)


class InviteInfo(BaseModel):
    """Returned to admin so they can hand the invite URL to the user.

    The URL itself is left for the client to assemble — server doesn't
    know its own external hostname reliably. Token + path is enough.

    Only used by the legacy invite-link flow (now opt-in). The default
    flow gives the user a starter password + force-change instead.
    """
    token: str
    accept_path: str = "/admin/accept-invite"
    expires_at: datetime


class CreateUserResponse(BaseModel):
    user: "UserListItem"
    # The starter password the admin should tell the new user. Forced
    # to be changed on first login. Server-side this matches
    # settings.default_new_user_password — echoed for admin convenience.
    default_password: str


class UserListItem(BaseModel):
    id: str
    email: str
    username: str | None
    display_name: str | None
    org_id: str | None
    org_slug: str | None
    is_super_admin: bool
    is_active: bool
    # True for the bootstrap admin account — UI uses this to hide
    # delete / demote / disable buttons since the backend rejects them
    # anyway.
    is_protected: bool = False
    has_password: bool
    has_pending_invite: bool
    invite_expires_at: datetime | None
    created_at: datetime


class UserListResponse(BaseModel):
    users: list[UserListItem]


class UpdateUserRequest(BaseModel):
    """All fields optional — admin patches what changed."""
    display_name: str | None = None
    is_active: bool | None = None
    is_super_admin: bool | None = None   # super_admin only
    org_slug: str | None = None          # super_admin only; "" to remove from org


class ResendInviteResponse(BaseModel):
    invite: InviteInfo


class ResetPasswordRequest(BaseModel):
    """Two modes:

    - Omit `new_password` (the safer default) → server generates an
      invite-style token; admin shares the URL with the user, who picks
      their own password on click.
    - Supply `new_password` → server hashes + sets it directly; the
      user is still forced to change on next login. Use when you have
      no good channel to send a URL but a trusted out-of-band one for
      a short string.
    """
    new_password: str | None = Field(default=None, min_length=8, max_length=128)


class ResetPasswordResponse(BaseModel):
    mode: Literal["invite_link", "direct"]
    # Present only when mode=invite_link. The admin formats the URL.
    invite: InviteInfo | None = None
    # Echoed back in `mode=direct` so admin can confirm what was set,
    # since they may have typed it in a small form.
    new_password: str | None = None


# Forward ref for CreateUserResponse.
CreateUserResponse.model_rebuild()
