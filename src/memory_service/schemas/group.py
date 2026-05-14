"""Request/response models for /v1/groups."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

_SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"

GroupRole = Literal["member", "admin"]


class GroupCreate(BaseModel):
    slug: str = Field(pattern=_SLUG_PATTERN, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    # Optional: super_admin can create a group in any org by setting
    # `org_slug`; regular org members can only create in their own org and
    # leave this blank (we infer from caller's user.org_id).
    org_slug: str | None = None


class GroupUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class GroupOut(BaseModel):
    id: str
    slug: str
    name: str
    # `None` when the parent org has been deleted (an "orphan" group).
    # Such groups are read-only + deletable; writes/member changes 409.
    org_slug: str | None
    status: Literal["ok", "orphan"] = "ok"
    created_at: datetime
    member_count: int


class GroupMemberAdd(BaseModel):
    user_id: str = Field(min_length=1)
    role: GroupRole = "member"


class GroupMemberRoleUpdate(BaseModel):
    role: GroupRole


class GroupMemberOut(BaseModel):
    user_id: str
    email: str
    display_name: str | None
    role: GroupRole
