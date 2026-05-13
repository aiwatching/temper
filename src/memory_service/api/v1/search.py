"""/v1/search — semantic search across episodes."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from memory_service.api.deps import CurrentUser, DBDep
from memory_service.core import memory
from memory_service.schemas.episode import SearchHitOut, SearchResponse

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResponse)
async def search(
    user: CurrentUser,
    db: DBDep,
    query: Annotated[str, Query(min_length=1, max_length=1000)],
    namespaces: Annotated[str | None, Query(description="Comma-separated list")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    as_of: Annotated[
        datetime | None,
        Query(
            description="ISO-8601 instant. Returns only facts that were "
            "active at this time — i.e. valid_at <= as_of and either "
            "invalid_at IS NULL or invalid_at > as_of."
        ),
    ] = None,
    edge_types: Annotated[
        str | None,
        Query(
            description="Comma-separated relation names "
            "(e.g. 'LIVES_IN,TEACHES'). Only RELATES_TO edges with these "
            "names are returned."
        ),
    ] = None,
    node_labels: Annotated[
        str | None,
        Query(
            description="Comma-separated entity labels "
            "(e.g. 'Person,Place'). Applied to entity-hit results."
        ),
    ] = None,
) -> SearchResponse:
    ns_list: list[str] | None
    if namespaces:
        ns_list = [n.strip() for n in namespaces.split(",") if n.strip()]
    else:
        ns_list = None
    et = [s.strip() for s in edge_types.split(",")] if edge_types else None
    nl = [s.strip() for s in node_labels.split(",")] if node_labels else None

    try:
        hits = await memory.search(
            user, query, ns_list, limit, db,
            as_of=as_of, edge_types=et, node_labels=nl,
        )
    except memory.MemoryError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc

    return SearchResponse(
        facts=[
            SearchHitOut(
                fact=h.fact,
                namespace=h.namespace,
                source_episode_ids=h.source_episode_ids,
                valid_at=h.valid_at,
                invalid_at=h.invalid_at,
                score=h.score,
                kind=h.kind,
            )
            for h in hits
        ],
        query=query,
        namespaces_searched=ns_list or [],
    )
