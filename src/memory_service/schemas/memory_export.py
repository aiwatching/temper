"""Per-user memory export/import bundle shapes.

Used by /v1/me/export and /v1/me/import. The point of this bundle is
to let a user (or super_admin on behalf of a user) move blocks +
documents + episodes between TEMPER instances — laptop dev → server,
old box → new box, etc.

What's IN the bundle:
  * blocks       — every MemoryBlock owned by the user (key + value +
                   metadata). typed_memory (tasks/focus/preferences)
                   is included implicitly since it's all backed by
                   blocks under reserved keys.
  * documents    — full markdown + path + tags + frontmatter.
  * episodes     — metadata + the **original content** retrieved from
                   graphiti by uuid. On import, the target side re-
                   extracts entities/facts with its own LLM/embed —
                   no vectors travel with the bundle.

What's NOT in the bundle:
  * users / api_keys / password hashes / SECRET_KEY-derived state
    (auth identity does not move with memory)
  * org / group / membership records (directory data — see
    admin_import for that)
  * FalkorDB graph structure (re-extracted on import)
  * embeddings (target dim may differ; re-embed on import)

Format version is a string and explicit so we can evolve without
breaking importers. Importers MUST reject bundles whose version they
don't recognize rather than silently mis-parsing.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

BUNDLE_FORMAT_VERSION = "1"


class ExportedBlock(BaseModel):
    """One MemoryBlock row. Round-trips losslessly except for the
    server-generated id (a fresh uuid is allocated on import)."""

    agent_slug: str = "*"
    key: str
    value: Any
    pinned: bool = False
    priority: int = 0
    description: str | None = None
    # Carried for posterity; import sets fresh timestamps.
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExportedDocument(BaseModel):
    """One Document row. Markdown content + addressing metadata.

    `namespace` and `path` together identify the document on import —
    they're the upsert key. Revisions are NOT included; if you want
    the edit history, snapshot pg_dump the document_revisions table
    separately."""

    namespace: str
    path: str
    title: str
    content: str
    content_type: str = "markdown"
    source: str | None = None
    source_url: str | None = None
    imported_at: datetime | None = None
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExportedEpisode(BaseModel):
    """One episode. Metadata from postgres + raw content from graphiti.

    `original_episode_id` is the graphiti uuid on the SOURCE side; we
    keep it for debugging / cross-referencing but the IMPORT side
    allocates a fresh uuid (graphiti owns episode identity)."""

    original_episode_id: str | None = None
    namespace: str
    content: str
    source_type: str = "text"
    source_description: str = ""
    tags: list[str] = Field(default_factory=list)
    reference_time: datetime | None = None
    created_by_agent: str = "imported"
    created_at: datetime | None = None


class BundleSource(BaseModel):
    """Informational provenance — never load-bearing for the import.
    Lets ops eyeball "where did this come from" without grep'ing."""

    host: str | None = None
    user_email: str | None = None
    user_id: str | None = None
    temper_version: str | None = None


class MemoryBundleV1(BaseModel):
    """The whole bundle — what /v1/me/export returns and /v1/me/import
    accepts. JSON-serializable."""

    format_version: Literal["1"] = BUNDLE_FORMAT_VERSION
    exported_at: datetime
    source: BundleSource = Field(default_factory=BundleSource)
    blocks: list[ExportedBlock] = Field(default_factory=list)
    documents: list[ExportedDocument] = Field(default_factory=list)
    episodes: list[ExportedEpisode] = Field(default_factory=list)

    @property
    def total_items(self) -> int:
        return len(self.blocks) + len(self.documents) + len(self.episodes)


class KindReport(BaseModel):
    """Per-kind tally on import. `inserted` + `merged` + `skipped` +
    `errored` should sum to the count of that kind in the bundle."""

    inserted: int = 0
    merged: int = 0
    skipped: int = 0
    errored: int = 0


class ImportError(BaseModel):
    kind: Literal["block", "document", "episode"]
    target: str  # block key, document path, or "episode #N"
    error: str


class ImportReport(BaseModel):
    """Result of POST /v1/me/import. Sums of kind reports tell you
    what the import did; `errors` lists the per-row failures (the
    import does NOT stop on first error)."""

    mode: Literal["merge", "replace"]
    skip_extraction: bool
    blocks: KindReport = Field(default_factory=KindReport)
    documents: KindReport = Field(default_factory=KindReport)
    episodes: KindReport = Field(default_factory=KindReport)
    errors: list[ImportError] = Field(default_factory=list)
    # Wall-clock seconds. For "should I worry it timed out?" debugging.
    duration_seconds: float = 0.0
