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


# Forward ref for CreateUserResponse.
CreateUserResponse.model_rebuild()
