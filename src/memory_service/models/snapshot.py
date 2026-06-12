"""MemorySnapshot — a point-in-time backup of one user's memory.

See migration 0015 for the design rationale. The `bundle` column holds
a MemoryBundleV1 (schemas/memory_export.py); the count + size columns
are denormalized so listing snapshots never loads the blob.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from memory_service.models._base import Base, UUIDPKMixin

# JSONB on Postgres, generic JSON on sqlite (tests).
from sqlalchemy import JSON

JSONColumn = JSON().with_variant(JSONB(), "postgresql")


class MemorySnapshot(Base, UUIDPKMixin):
    __tablename__ = "memory_snapshots"

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    # "auto" (scheduler) | "manual" (user-triggered).
    kind: Mapped[str] = mapped_column(String(16))
    bundle: Mapped[dict[str, Any]] = mapped_column(JSONColumn)

    # Defaulted columns after the required ones (dataclass ordering).
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None,
    )
    include_episodes: Mapped[bool] = mapped_column(Boolean, default=False)
    blocks_count: Mapped[int] = mapped_column(Integer, default=0)
    documents_count: Mapped[int] = mapped_column(Integer, default=0)
    episodes_count: Mapped[int] = mapped_column(Integer, default=0)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(Text, default=None)
