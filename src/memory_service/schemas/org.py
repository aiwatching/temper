"""Request/response models for /v1/orgs."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# Slug rules: lowercase letters, digits, single hyphens. Matches the
# `org:<slug>` convention in namespace strings.
_SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"


class OrgCreate(BaseModel):
    slug: str = Field(pattern=_SLUG_PATTERN, max_length=64)
    name: str = Field(min_length=1, max_length=255)


class OrgUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class OrgOut(BaseModel):
    id: str
    slug: str
    name: str
    created_at: datetime
    member_count: int


class OrgMemberAdd(BaseModel):
    user_id: str = Field(min_length=1)


class OrgMemberOut(BaseModel):
    user_id: str
    email: str
    display_name: str | None
