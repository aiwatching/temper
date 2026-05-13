"""Request/response models for API key management."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CreateAPIKeyRequest(BaseModel):
    agent_name: str = Field(min_length=1, max_length=128)


class APIKeyResponse(BaseModel):
    """Stored representation — never includes the plaintext key."""

    id: str
    agent_name: str
    prefix: str
    revoked: bool
    created_at: datetime
    last_used_at: datetime | None


class APIKeyCreatedResponse(APIKeyResponse):
    """Returned only on creation. The `key` field is shown once and never again."""

    key: str
