"""EpisodeMetadata — application-layer record of every Graphiti episode.

Graphiti owns the actual knowledge-graph data (entities, facts, episodes)
inside FalkorDB. We mirror just the bits we need at the API surface:

  - who created it (user + agent name)
  - which namespace it belongs to (for permission checks)
  - the user-supplied tags
  - the Graphiti UUID so we can look the rest up on demand

`id` is the Graphiti episodic-node UUID, set explicitly at insert time.
We don't use the UUIDPKMixin here because the value originates from
Graphiti, not from us.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from memory_service.models._base import Base, TimestampMixin


class EpisodeMetadata(Base, TimestampMixin):
    __tablename__ = "episode_metadata"

    # Graphiti's episodic-node UUID (string). Doubles as our row ID.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    namespace: Mapped[str] = mapped_column(String(128), index=True)
    created_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    # The agent name the API key was registered under (denormalised for audit).
    created_by_agent: Mapped[str] = mapped_column(String(128))
    source_type: Mapped[str] = mapped_column(String(16), default="text")
    # Free-form labels. Stored as JSON to keep portability across pg / sqlite.
    tags: Mapped[list[str]] = mapped_column(JSON, default_factory=list)
    reference_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
