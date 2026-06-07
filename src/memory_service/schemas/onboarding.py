"""One-shot user provisioning for external onboarding flows.

Used by /v1/onboarding/provision — a single call that, given a few
identity fields from an external onboarding system (Forge, internal
HRIS, whatever), brings up a fresh TEMPER user end-to-end:

  1. Org for `company` (created if missing)
  2. Group for `dept` within that org (created if missing)
  3. User row with a starter password (must_change_password=True)
  4. API key scoped to the new user's `username`

The response carries the plaintext starter password and the
plaintext API key. Both are returned ONLY on this call — neither is
retrievable later. The caller is expected to immediately persist /
display them to the user being onboarded.
"""
from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class ProvisionRequest(BaseModel):
    # Login alias. Also used as the API key's agent_slug, which scopes
    # the user's default memory namespace to `agent:<user_id>/<username>`.
    username: str = Field(min_length=1, max_length=64)
    email: EmailStr
    # Free-text human label for the org. Slug derived from this on
    # the server; the slug is what TEMPER uses internally.
    company: str = Field(min_length=1, max_length=255)
    # Free-text human label for the dept/team. Slug derived as
    # `<org_slug>-<dept_slug>` to dodge cross-org collisions (groups
    # have a globally-unique slug in this schema).
    dept: str = Field(min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)


class ProvisionCreatedFlags(BaseModel):
    """Per-resource flag: true = freshly created, false = reused
    existing row. Useful for the caller to log audit context."""
    org: bool
    group: bool
    user: bool


class ProvisionResponse(BaseModel):
    user_id: str
    username: str
    email: str
    display_name: str | None
    org_slug: str
    group_slug: str

    # Starter password. Plaintext. Must be relayed to the user; they
    # are forced to change it on first login (must_change_password=true).
    default_password: str
    must_change_password: bool = True

    # Plaintext API key (mk_...). Returned ONLY here — TEMPER stores
    # only its hash. agent_slug is the username so namespace = agent:<uid>/<username>.
    api_key: str
    api_key_prefix: str
    api_key_agent_slug: str

    created: ProvisionCreatedFlags
