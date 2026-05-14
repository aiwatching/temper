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
    display_name: str | None = Field(default=None, max_length=255)
    org_slug: str | None = None
    is_org_admin: bool = False
    # Super_admin can supply this; org_admin always gets False forced.
    is_super_admin: bool = False
    group_slugs: list[str] = Field(default_factory=list)


class InviteInfo(BaseModel):
    """Returned to admin so they can hand the invite URL to the user.

    The URL itself is left for the client to assemble — server doesn't
    know its own external hostname reliably. Token + path is enough.
    """
    token: str
    accept_path: str = "/admin/accept-invite"
    expires_at: datetime


class CreateUserResponse(BaseModel):
    user: "UserListItem"
    invite: InviteInfo


class UserListItem(BaseModel):
    id: str
    email: str
    display_name: str | None
    org_slug: str | None
    is_super_admin: bool
    is_org_admin: bool
    is_active: bool
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
    is_org_admin: bool | None = None     # super_admin or relevant org_admin
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
