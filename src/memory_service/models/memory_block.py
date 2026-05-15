"""memory_blocks — structured key/value memory for first-person assertions.

This is the storage Graphiti is NOT good at: durable user preferences,
identity facts, working state, daily routines — anything where the user
asserts something about themselves and expects the agent to honor it
verbatim across sessions. See `db/migrations/versions/0012_memory_blocks.py`
for the schema rationale and why Graphiti doesn't fit this class of data.

The (user, agent, key) triple is unique. `agent_slug='*'` means the
block is global — every agent under this user sees it on read.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from memory_service.models._base import Base, TimestampMixin, UUIDPKMixin


GLOBAL_AGENT_SLUG = "*"


class MemoryBlock(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "memory_blocks"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "agent_slug", "block_key",
            name="uq_memory_blocks_user_agent_key",
        ),
    )

    # Required fields (no defaults) must come first per @dataclass rules.
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    block_key: Mapped[str] = mapped_column(String(255))
    # JSONB. Caller decides shape — service treats as opaque.
    block_value: Mapped[Any] = mapped_column(JSONB)

    # Defaulted fields come after.
    # '*' sentinel = global block, visible to every agent under this user.
    # Stored NOT NULL so the UNIQUE works (Postgres treats NULL != NULL).
    agent_slug: Mapped[str] = mapped_column(
        String(64),
        default=GLOBAL_AGENT_SLUG,
    )
    # Pinned blocks are auto-injected into the agent's system prompt
    # every turn. Use sparingly — total pinned size goes into every
    # prompt's tokens.
    pinned: Mapped[bool] = mapped_column(default=False)
    # Higher priority shows first when multiple pinned blocks render.
    priority: Mapped[int] = mapped_column(Integer, default=0)
    # One-liner shown to the agent on read so each block is self-
    # documenting (e.g. "what the user calls me").
    description: Mapped[str | None] = mapped_column(Text, default=None)
    # Informational: "agent:smith", "user:admin-ui", "system". Not enforced.
    updated_by: Mapped[str | None] = mapped_column(String(128), default=None)
