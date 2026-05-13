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

router = APIRouter(prefix="/admin", tags=["admin"])


class BuildCommunitiesResponse(BaseModel):
    namespace: str
    communities_created: int
    community_edges_created: int


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


@router.post("/communities/build", response_model=BuildCommunitiesResponse)
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
) -> BuildCommunitiesResponse:
    """Run Graphiti's clustering on a namespace's entities, producing
    Community nodes + edges that summarize related neighborhoods.

    Idempotent-ish: re-running re-evaluates clusters from the current
    graph state. Existing Communities aren't automatically pruned —
    they stay until explicitly removed.
    """
    try:
        result = await memory.build_communities(user, namespace, db)
    except memory.MemoryError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    return BuildCommunitiesResponse(**result)
