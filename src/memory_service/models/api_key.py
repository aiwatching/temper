"""API key — the credential agents present to access the service.

The plaintext key is **only** returned once at creation time. We persist
the SHA-256 hash plus a short `prefix` for human-friendly listing
(e.g. "mk_abc1…"). Revoked keys keep their row so audit trails survive.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from memory_service.models._base import Base, TimestampMixin, UUIDPKMixin


class APIKey(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("user_id", "agent_slug", name="uq_api_keys_user_agent_slug"),
    )

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Human-readable name given by the user — "english-agent", "ci-pipeline", …
    agent_name: Mapped[str] = mapped_column(String(128))
    # Stored as SHA-256 hex digest of the plaintext key. We use SHA-256 (not
    # bcrypt) because API keys are high-entropy already and we want O(1)
    # lookup; bcrypt would force a full table scan + per-row hash on auth.
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # First 8 chars of the plaintext, shown in the UI: "mk_a1b2…"
    prefix: Mapped[str] = mapped_column(String(16))
    # Optional routing key — when set, requests authed by this API key default
    # to namespace `agent:<user_id>/<agent_slug>` instead of flat `user:<id>`,
    # so different agents under one user don't share memory by default. Two
    # API keys with the same (user_id, agent_slug) → same sub-namespace =
    # explicit memory sharing. NULL = legacy / unscoped (writes to user:<id>).
    agent_slug: Mapped[str | None] = mapped_column(String(64), default=None)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
