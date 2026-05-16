"""Wire shapes for the documents primitive."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ContentType = Literal["markdown", "text", "json", "html"]


class DocumentOut(BaseModel):
    id: str
    user_id: str
    namespace: str
    path: str
    title: str
    content: str
    content_type: ContentType
    source: str | None = None
    source_url: str | None = None
    imported_at: datetime | None = None
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    word_count: int = 0
    created_at: datetime
    updated_at: datetime
    updated_by: str | None = None


class DocumentSummary(BaseModel):
    """Lighter shape — body content elided, used for list endpoints."""
    id: str
    namespace: str
    path: str
    title: str
    content_type: ContentType
    source: str | None = None
    source_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    word_count: int = 0
    snippet: str | None = None    # populated by search results
    updated_at: datetime


class DocumentListResponse(BaseModel):
    documents: list[DocumentSummary]
    next_cursor: str | None = None


class UpsertDocumentRequest(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    content: str = ""
    content_type: ContentType = "markdown"
    source: str | None = Field(default=None, max_length=64)
    source_url: str | None = None
    imported_at: datetime | None = None
    frontmatter: dict[str, Any] | None = None
    tags: list[str] | None = None
    namespace: str | None = Field(
        default=None,
        description=(
            "Target namespace. Defaults to the caller's primary scope "
            "(user:<id> for session auth, agent:<id>/<slug> for API "
            "keys with an agent slug)."
        ),
    )
    reason: str | None = Field(
        default=None,
        description="Free-form revision reason — stored on the revision row.",
    )


class PatchDocumentRequest(BaseModel):
    title: str | None = None
    content: str | None = None
    content_type: ContentType | None = None
    source: str | None = None
    source_url: str | None = None
    imported_at: datetime | None = None
    frontmatter: dict[str, Any] | None = None
    tags: list[str] | None = None
    # Patch-specific verbs. When provided, these override `content`:
    #   append: appends to the end with a newline boundary
    #   prepend: prepends to the start
    #   replace: { find, replace } — first occurrence only
    append: str | None = None
    prepend: str | None = None
    replace: dict[str, str] | None = None
    reason: str | None = None


class SearchHit(BaseModel):
    path: str
    namespace: str
    title: str
    snippet: str
    score: float
    tags: list[str] = Field(default_factory=list)
    source: str | None = None
    source_url: str | None = None


class SearchResponse(BaseModel):
    hits: list[SearchHit]
    kind: Literal["fts", "vector", "hybrid"]
    query: str


class BacklinkRow(BaseModel):
    source_namespace: str
    source_path: str
    source_title: str
    label: str | None = None


class BacklinkResponse(BaseModel):
    target_path: str
    target_namespace: str
    backlinks: list[BacklinkRow]


class RevisionSummary(BaseModel):
    id: str
    revised_at: datetime
    revised_by: str | None
    reason: str | None
    title: str


class RevisionDetail(RevisionSummary):
    content: str
    frontmatter: dict[str, Any] | None


class RevisionListResponse(BaseModel):
    revisions: list[RevisionSummary]


class ImportItem(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    title: str | None = None
    content: str
    content_type: ContentType = "markdown"
    source: str | None = None
    source_url: str | None = None
    imported_at: datetime | None = None
    frontmatter: dict[str, Any] | None = None
    tags: list[str] | None = None


class ImportRequest(BaseModel):
    namespace: str | None = None
    items: list[ImportItem]


class ImportResponse(BaseModel):
    imported: int
    skipped: int
    skipped_paths: list[str] = Field(default_factory=list)
