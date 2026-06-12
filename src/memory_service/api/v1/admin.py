"""/v1/admin — operator-side endpoints for graph maintenance.

These trigger long-running or expensive jobs that don't fit the normal
read/write CRUD; they're scoped per-namespace and gated by the
namespace's write permission so namespace owners can do them themselves
without involving super_admin.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from memory_service.api.deps import CurrentUser, DBDep
from memory_service.core import memory
from memory_service.core.admin_import import run_import
from memory_service.schemas.admin_import import (
    BulkImportRequest,
    BulkImportResponse,
)

router = APIRouter(prefix="/admin", tags=["admin"])


class CommunityBuildStatus(BaseModel):
    namespace: str
    # idle (never run) | running | done | failed
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    # Present once status == done.
    communities_created: int | None = None
    community_edges_created: int | None = None


class ReindexEmbeddingsResponse(BaseModel):
    namespace: str
    entities_reindexed: int
    communities_reindexed: int
    failed: int


@router.post(
    "/embeddings/reindex", response_model=ReindexEmbeddingsResponse
)
async def reindex_embeddings(
    user: CurrentUser,
    db: DBDep,
    namespace: Annotated[
        str | None,
        Query(description="Target namespace. Defaults to user:me."),
    ] = None,
    include_communities: Annotated[
        bool,
        Query(
            description="Also re-embed Community nodes — they have name "
            "embeddings too and become stale alongside entities."
        ),
    ] = False,
) -> ReindexEmbeddingsResponse:
    """Re-embed every Entity (and optionally Community) node in a namespace.

    Run this after changing EMBEDDING_PROVIDER or EMBEDDING_MODEL. Until
    you do, semantic search will be comparing new query vectors against
    stale node vectors — same field name, different vector space.

    Synchronous: the request blocks until done (seconds for small
    namespaces, minutes for big ones). Requires WRITE permission.
    """
    try:
        result = await memory.reindex_embeddings(
            user, namespace, db, include_communities=include_communities
        )
    except memory.MemoryError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    return ReindexEmbeddingsResponse(**result)


@router.post("/import", response_model=BulkImportResponse)
async def bulk_import(
    payload: BulkImportRequest, user: CurrentUser, db: DBDep,
) -> BulkImportResponse:
    """Bulk-create orgs / groups / users + assignments in one call.

    Super_admin only. Use `dry_run: true` first to validate without
    mutating — the response lists everything that *would* happen plus
    any errors. Generated passwords for new users come back in
    `created_users`; copy them somewhere safe before closing the
    response (they're not retrievable).
    """
    if not user.is_super_admin:
        raise HTTPException(status_code=403, detail="Bulk import requires super_admin")
    return await run_import(payload, db)


class ResummarizeEntityResponse(BaseModel):
    id: str
    namespace: str
    name: str | None
    summary_before: str
    summary_after: str
    source_episode_count: int
    note: str | None = None


@router.post(
    "/entities/{entity_uuid}/resummarize",
    response_model=ResummarizeEntityResponse,
)
async def resummarize_entity(
    entity_uuid: str, user: CurrentUser, db: DBDep,
) -> ResummarizeEntityResponse:
    """Rebuild an entity's `.summary` from its source episodes via LLM.

    Use this when an entity's summary has accumulated stale or wrong
    text — typically because Graphiti's normal summary-update path
    short-circuits the LLM and only appends new edge facts to whatever
    was there before. This endpoint pulls every episode that mentions
    the entity, hands them to Graphiti's
    `extract_entity_summaries_from_episodes` prompt, and overwrites
    `.summary` with the LLM's fresh take.

    Cost: one LLM call. Requires WRITE on the entity's namespace.
    Respects sleeping namespaces.
    """
    try:
        result = await memory.resummarize_entity(user, entity_uuid, db)
    except memory.MemoryError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail=f"Entity {entity_uuid} not found")
    return ResummarizeEntityResponse(**result)


@router.post(
    "/communities/build",
    response_model=CommunityBuildStatus,
    status_code=202,
)
async def build_communities(
    user: CurrentUser,
    db: DBDep,
    namespace: Annotated[
        str | None,
        Query(
            description="Target namespace. Defaults to caller's own "
            "user:<id>. Requires write permission on the namespace."
        ),
    ] = None,
) -> CommunityBuildStatus:
    """Start Graphiti's clustering on a namespace's entities, producing
    Community nodes + edges that summarize related neighborhoods.

    Runs in the BACKGROUND and returns 202 immediately — the build
    hammers FalkorDB's single worker for minutes, so blocking the
    request (and piling on concurrent builds) is what wedged the
    service before. Poll GET /v1/admin/communities/build/status to see
    when it finishes. A second build on a namespace that's already
    building gets 409.

    Idempotent-ish: re-running re-evaluates clusters from the current
    graph state. Existing Communities aren't auto-pruned.
    """
    try:
        result = await memory.start_community_build(user, namespace, db)
    except memory.MemoryError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    return CommunityBuildStatus(**result)


@router.get("/communities/build/status", response_model=CommunityBuildStatus)
async def build_communities_status(
    user: CurrentUser,
    db: DBDep,
    namespace: Annotated[
        str | None,
        Query(description="Namespace to check. Defaults to caller's own user:<id>."),
    ] = None,
) -> CommunityBuildStatus:
    """Poll the latest community build for a namespace."""
    try:
        result = await memory.community_build_status(user, namespace, db)
    except memory.MemoryError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    return CommunityBuildStatus(**result)
