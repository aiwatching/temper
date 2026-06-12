"""Request/response models for API key management."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CreateAPIKeyRequest(BaseModel):
    agent_name: str = Field(min_length=1, max_length=128)
    # When set, requests via this key default to namespace
    # `agent:<user_id>/<agent_slug>`. Multiple keys with the same slug
    # (same user) share memory — that's how you give several agents /
    # machines their own credential for one namespace. Leave null to
    # keep the legacy unscoped behaviour (writes to user:<id>).
    #
    # NOT pattern-validated: the server slugifies whatever you send
    # (lowercase, hyphenate, trim) so input is lenient and a slug is
    # never stored in a form the namespace parser would reject.
    agent_slug: str | None = Field(default=None, max_length=128)


class APIKeyResponse(BaseModel):
    """Stored representation — never includes the plaintext key."""

    id: str
    agent_name: str
    agent_slug: str | None = None
    prefix: str
    revoked: bool
    created_at: datetime
    last_used_at: datetime | None


class APIKeyCreatedResponse(APIKeyResponse):
    """Returned only on creation. The `key` field is shown once and never again."""

    key: str


class AdminAPIKeyListItem(APIKeyResponse):
    """Shape for the admin all-keys view. Includes the owning user's
    identifiers so the UI can group / display per-user."""
    user_id: str
    user_email: str
    user_username: str | None


class APIKeyUpdateRequest(BaseModel):
    """super_admin toggles a key's revoked state. The auth path filters
    on `revoked=False`, so flipping back to False reactivates the key."""
    revoked: bool


class APIKeyScopeUpdate(BaseModel):
    """Owner (or super_admin) rebinds the key's agent_slug. Send
    null to clear the scope (key becomes legacy / unscoped, writes go
    to flat user:<id>); send a slug to switch / set the scope.

    The key plaintext is unchanged — agents holding it keep working —
    but their future writes/reads route to the new namespace. Data
    written under the old slug stays where it was (still readable via
    explicit `namespace=agent:<id>/<old-slug>` and via the user's
    default cross-agent search).
    """
    # Not pattern-validated — the server slugifies it (see
    # core.namespaces.slugify_agent_slug). Send null / "" to clear the
    # scope. Anything else is normalized to a valid slug.
    agent_slug: str | None = Field(..., max_length=128)
