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
from sqlalchemy.dialects.postgresql import ARRAY
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
    # "done" for synchronous writes (the historic default), "pending" when
    # POST /v1/episodes returned 202 and is still extracting, "failed" when
    # the background extraction raised. Synchronous writes never go
    # through pending — they either succeed (done) or roll back the row.
    extraction_status: Mapped[str] = mapped_column(String(16), default="done")
    extraction_error: Mapped[str | None] = mapped_column(String(2048), default=None)
    # In sync writes this is identical to `id`. In async writes `id` is a
    # tracking UUID we generate up front (so the API can return immediately);
    # Graphiti picks its own UUID during background extraction and we record
    # it here. get_episode / delete_episode resolve through this column when
    # talking to FalkorDB.
    graphiti_episode_id: Mapped[str | None] = mapped_column(
        String(64), default=None, index=True
    )
    # Documents this episode references via [[wikilink]]. Populated by
    # the typed memory write paths (note_event, task_*) when their
    # content includes wikilinks; drives the recall fan-out that
    # surfaces linked documents alongside recalled episodes.
    # Stored as ARRAY on Postgres; on sqlite (tests only) we use a
    # JSON-backed list via the generic JSON column type.
    linked_document_paths: Mapped[list[str] | None] = mapped_column(
        JSON().with_variant(ARRAY(String(512)), "postgresql"),
        default=None,
    )
    # SHA-256 of the submitted content — powers the write-dedup guard
    # (same namespace + same hash within the window → acknowledged but
    # not re-extracted). NULL on pre-0014 rows; they never dedup-match.
    content_sha256: Mapped[str | None] = mapped_column(
        String(64), default=None,
    )
    # Extraction yield recorded at write time so stats can report
    # zero-yield episodes without querying the graph. NULL = unknown
    # (pre-0014 row, or async extraction not finished yet).
    extracted_entities_count: Mapped[int | None] = mapped_column(default=None)
    extracted_facts_count: Mapped[int | None] = mapped_column(default=None)
