"""Request/response models for episode + search endpoints."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class EntityOut(BaseModel):
    uuid: str
    name: str
    labels: list[str] = []
    summary: str | None = None


class FactOut(BaseModel):
    uuid: str
    fact: str
    source_entity_uuid: str | None = None
    target_entity_uuid: str | None = None
    valid_at: datetime | None = None
    invalid_at: datetime | None = None


class CreateEpisodeRequest(BaseModel):
    namespace: str | None = Field(
        default=None,
        description="user:..., group:..., org:..., or public. Defaults to user:{caller}.",
    )
    content: str = Field(min_length=1, max_length=64_000)
    source_type: Literal["message", "text", "json"] = "text"
    source_description: str | None = None
    reference_time: datetime | None = None
    tags: list[str] | None = None
    saga: str | None = Field(
        default=None,
        description="Optional saga name. Episodes sharing a name in the "
        "same namespace get chained via NEXT_EPISODE edges — useful for "
        "importing chat transcripts as one logical conversation.",
    )


class CreateEpisodeResponse(BaseModel):
    episode_id: str
    namespace: str
    extracted_entities: list[EntityOut]
    extracted_facts: list[FactOut]
    created_at: datetime
    # True when the server acknowledged but didn't extract: content
    # below the quality floor (episode_id == "") or a dedup hit
    # (episode_id == the existing episode's id). skip_reason explains
    # which. Legacy writers that ignore unknown fields are unaffected.
    skipped: bool = False
    skip_reason: str | None = None


class BulkEpisodeItem(BaseModel):
    content: str = Field(min_length=1, max_length=64_000)
    source_type: Literal["message", "text", "json"] = "text"
    source_description: str | None = None
    reference_time: datetime | None = None
    tags: list[str] | None = None


class BulkEpisodesRequest(BaseModel):
    namespace: str | None = Field(
        default=None,
        description="Shared namespace for all items. Defaults to user:me.",
    )
    saga: str | None = Field(
        default=None,
        description="Shared saga name across all items in this batch.",
    )
    items: list[BulkEpisodeItem] = Field(min_length=1, max_length=200)


class BulkEpisodesResponse(BaseModel):
    episode_ids: list[str]
    namespace: str
    total_entities: int
    total_facts: int
    # Items dropped by the quality floor / dedup window; episode_ids
    # excludes them.
    skipped_count: int = 0


class EpisodeStatusResponse(BaseModel):
    episode_id: str
    extraction_status: Literal["pending", "done", "failed"]
    extraction_error: str | None = None


class EpisodeSummary(BaseModel):
    """Compact representation used in list endpoints."""

    episode_id: str
    namespace: str
    created_by_user_id: str
    created_by_agent: str
    source_type: str
    tags: list[str]
    reference_time: datetime | None
    created_at: datetime


class EpisodeListResponse(BaseModel):
    episodes: list[EpisodeSummary]
    next_cursor: datetime | None = None


class EpisodeDetailResponse(BaseModel):
    episode_id: str
    namespace: str
    content: str | None
    created_by_user_id: str
    created_by_agent: str
    source_type: str
    tags: list[str]
    reference_time: datetime | None
    created_at: datetime
    entities: list[EntityOut]
    facts: list[FactOut]


class SearchHitOut(BaseModel):
    fact: str
    namespace: str
    source_episode_ids: list[str]
    valid_at: datetime | None
    invalid_at: datetime | None
    score: float | None = None
    kind: Literal["fact", "entity", "community"] = "fact"
    # `id` is the edge UUID for fact hits and the entity UUID for entity
    # hits. Surfaced so agents can pass it back to PATCH /v1/facts/<id>
    # or POST /v1/admin/entities/<id>/resummarize without an extra lookup.
    id: str | None = None
    source_node_uuid: str | None = None
    target_node_uuid: str | None = None


class SearchResponse(BaseModel):
    facts: list[SearchHitOut]
    query: str
    namespaces_searched: list[str]
