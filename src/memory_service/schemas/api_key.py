"""Request/response models for API key management."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


_AGENT_SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"


class CreateAPIKeyRequest(BaseModel):
    agent_name: str = Field(min_length=1, max_length=128)
    # When set, requests via this key default to namespace
    # `agent:<user_id>/<agent_slug>`. Two keys with the same slug (same user)
    # share memory — that's how you opt into cross-agent recall on purpose.
    # Leave null to keep the legacy unscoped behaviour (writes to user:<id>).
    agent_slug: str | None = Field(
        default=None, pattern=_AGENT_SLUG_PATTERN, max_length=64
    )


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
