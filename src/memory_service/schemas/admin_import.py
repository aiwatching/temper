"""Bulk-import payload shape for /v1/admin/import.

Lets super_admin describe a whole org structure in one JSON document:
orgs, groups, users + their assignments. Useful for spinning up a real
team without 30 separate API calls.

Schema deliberately mirrors how a small company actually thinks about
its directory: top-level lists of orgs / groups / users, with users
referencing org_slug and group_slug they should join.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class ImportOrg(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$", max_length=64)
    name: str = Field(min_length=1, max_length=255)


class ImportGroup(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$", max_length=64)
    name: str = Field(min_length=1, max_length=255)
    # Either supply the org explicitly here, or omit and inherit from
    # users that reference this group (we error on ambiguity).
    org_slug: str | None = None


class ImportUserGroupMembership(BaseModel):
    slug: str


class ImportUser(BaseModel):
    email: EmailStr
    display_name: str | None = None
    # If omitted at import time, a random one is generated and returned
    # in the response so the operator can share it with the user.
    password: str | None = Field(default=None, min_length=8, max_length=128)
    org_slug: str | None = None
    groups: list[ImportUserGroupMembership] = Field(default_factory=list)


class BulkImportRequest(BaseModel):
    dry_run: bool = Field(
        default=False,
        description="Validate + plan without mutating. The response shows what would happen.",
    )
    orgs: list[ImportOrg] = Field(default_factory=list)
    groups: list[ImportGroup] = Field(default_factory=list)
    users: list[ImportUser] = Field(default_factory=list)


class CreatedUser(BaseModel):
    email: str
    user_id: str
    # Only present when we generated the password ourselves — copy it
    # somewhere safe before the response disappears.
    generated_password: str | None = None


class ImportResultRow(BaseModel):
    kind: Literal["org", "group", "user", "membership"]
    target: str  # slug, email, or "user→group"
    action: Literal["created", "updated", "skipped", "would-create", "would-update", "would-skip"]
    detail: str | None = None


class BulkImportResponse(BaseModel):
    dry_run: bool
    created_users: list[CreatedUser]
    rows: list[ImportResultRow]
    errors: list[str]
