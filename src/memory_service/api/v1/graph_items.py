"""GET /v1/entities/{uuid} and GET /v1/facts/{uuid}.

Look up a single entity node or RELATES_TO fact edge by UUID. We search
across the caller's readable namespaces and return the first hit — UUIDs
are globally unique within Graphiti, so at most one namespace can own
the record we want.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from memory_service.api.deps import CurrentUser, DBDep
from memory_service.core import memory

router = APIRouter(tags=["graph-items"])


class EntityResponse(BaseModel):
    id: str
    namespace: str
    name: str | None
    summary: str | None
    labels: list[str]
    created_at: datetime | None
    attributes: dict


class FactResponse(BaseModel):
    id: str
    namespace: str
    fact: str
    name: str | None
    source_uuid: str
    target_uuid: str
    source_name: str | None
    target_name: str | None
    valid_at: datetime | None
    invalid_at: datetime | None
    created_at: datetime | None
    episodes: list[str]


@router.get("/entities/{entity_uuid}", response_model=EntityResponse)
async def get_entity(
    entity_uuid: str, user: CurrentUser, db: DBDep
) -> EntityResponse:
    try:
        data = await memory.get_entity(user, entity_uuid, db)
    except memory.MemoryError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    if data is None:
        # 404 covers both "doesn't exist" and "exists but in a namespace
        # you can't read" — same as /v1/episodes/{id}, doesn't leak.
        raise HTTPException(status_code=404, detail=f"Entity {entity_uuid} not found")
    return EntityResponse(**data)


@router.get("/facts/{fact_uuid}", response_model=FactResponse)
async def get_fact(
    fact_uuid: str, user: CurrentUser, db: DBDep
) -> FactResponse:
    try:
        data = await memory.get_fact(user, fact_uuid, db)
    except memory.MemoryError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    if data is None:
        raise HTTPException(status_code=404, detail=f"Fact {fact_uuid} not found")
    return FactResponse(**data)
