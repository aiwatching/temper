"""documents — long-form markdown content (notes / wiki / SOPs).

The fourth memory primitive alongside episodes, memory_blocks, and
the typed memory layer that wraps them. See
`docs/notes-primitive-proposal.md` and migration 0013_documents
for the design rationale.

Storage model:
  * One table per user, partitioned by namespace
    (`user:<uuid>` / `agent:<uuid>/<slug>` / `group:<slug>`).
  * Stable `path` per (user, namespace) — filesystem-style
    ("projects/auth/refactor") or dotted ("status.weekly-report-2026-w20").
  * `content` is markdown (default), `text`, `json`, or `html`.
  * `source_url` + `imported_at` + `source` are first-class so
    "all my Mantis imports last week" is an indexed query.
  * Frontmatter + tags are auxiliary metadata for filtering / display.

Search infrastructure:
  * `content_tsv` (Postgres TSVECTOR) is maintained by trigger;
    title gets weight A, body weight B.
  * `embedding` column is reserved for N2 (pgvector).

Document <-> Document edges live in `document_links` (parsed from
`[[wikilink]]` on save). Episode <-> Document edges live on
`episode_metadata.linked_document_paths` (a Postgres array).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from memory_service.models._base import Base, TimestampMixin, UUIDPKMixin


class Document(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "namespace", "path",
            name="uq_documents_user_namespace_path",
        ),
        CheckConstraint(
            "content_type IN ('markdown','text','json','html')",
            name="ck_documents_content_type",
        ),
        Index("ix_documents_user_namespace", "user_id", "namespace"),
        Index("ix_documents_path_pattern", "user_id", "namespace", "path"),
    )

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    namespace: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(String(512))
    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, default="")
    content_type: Mapped[str] = mapped_column(String(32), default="markdown")

    # Import metadata — first-class for indexed lookup.
    source: Mapped[str | None] = mapped_column(String(64), default=None)
    source_url: Mapped[str | None] = mapped_column(Text, default=None)
    imported_at: Mapped[datetime | None] = mapped_column(default=None)

    # Auxiliary. JSONB is used unconditionally — documents is a
    # Postgres-only feature (the FTS, GIN, tsvector all require it).
    frontmatter: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict,
    )
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(64)), default=list,
    )

    # Search columns. content_tsv is maintained by a trigger; we
    # never write to it from the ORM, just read.
    content_tsv: Mapped[Any] = mapped_column(TSVECTOR, default="")
    # Reserved — embedding pipeline lands in N2. BYTEA for now (we'll
    # switch to pgvector's VECTOR type when the extension is required).
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)

    word_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_by: Mapped[str | None] = mapped_column(String(128), default=None)


class DocumentLink(Base):
    """Materialized [[wikilink]] target — one row per (source, target)."""
    __tablename__ = "document_links"
    __table_args__ = (
        Index(
            "ix_document_links_target",
            "target_namespace", "target_path",
        ),
    )

    source_document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    target_path: Mapped[str] = mapped_column(String(512), primary_key=True)
    # Defaulted fields go after non-defaulted (dataclass ordering).
    # NULL on target_namespace means "same namespace as the source
    # document"; explicit value supports cross-namespace links.
    target_namespace: Mapped[str | None] = mapped_column(
        String(255), primary_key=True, default=None,
    )
    label: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime | None] = mapped_column(default=None)


class DocumentRevision(Base, UUIDPKMixin):
    """Edit trail for documents.

    Pruned by a future consolidation pass — for now we keep all
    revisions. Each revision is a full snapshot, not a diff, so
    recovery is read-only and trivial.
    """
    __tablename__ = "document_revisions"
    __table_args__ = (
        Index(
            "ix_document_revisions_doc_time",
            "document_id", "revised_at",
        ),
    )

    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
    )
    title: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    # Defaulted fields below — dataclass ordering rule.
    frontmatter: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, default=None,
    )
    revised_at: Mapped[datetime | None] = mapped_column(default=None)
    revised_by: Mapped[str | None] = mapped_column(String(128), default=None)
    reason: Mapped[str | None] = mapped_column(Text, default=None)
