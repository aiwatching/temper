"""GET /v1/entities/{uuid} and GET /v1/facts/{uuid}.

Look up a single entity node or RELATES_TO fact edge by UUID. We search
across the caller's readable namespaces and return the first hit — UUIDs
are globally unique within Graphiti, so at most one namespace can own
the record we want.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
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


class InvalidateFactRequest(BaseModel):
    # `None` means "reactivate this fact" — undo a prior invalidation.
    invalid_at: datetime | None = None


class InvalidateFactResponse(BaseModel):
    id: str
    namespace: str
    fact: str
    valid_at: datetime | None
    invalid_at: datetime | None


@router.patch("/facts/{fact_uuid}", response_model=InvalidateFactResponse)
async def invalidate_fact(
    fact_uuid: str,
    payload: InvalidateFactRequest,
    user: CurrentUser,
    db: DBDep,
) -> InvalidateFactResponse:
    """Explicitly set or clear `invalid_at` on a fact.

    Overrides whatever Graphiti's contradiction inference decided. Pass
    `null` for invalid_at to reactivate a fact. Defaults to "now" when
    the field is omitted by the client (Pydantic treats omitted vs null
    the same here, so callers wanting "now" should send the timestamp
    explicitly).
    """
    try:
        data = await memory.set_fact_invalid_at(
            user, fact_uuid, payload.invalid_at, db
        )
    except memory.MemoryError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    if data is None:
        raise HTTPException(status_code=404, detail=f"Fact {fact_uuid} not found")
    return InvalidateFactResponse(**data)


@router.delete("/facts/{fact_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_fact(
    fact_uuid: str, user: CurrentUser, db: DBDep
) -> None:
    """Hard-delete a fact. Different from PATCHing invalid_at — the row
    is gone, no time-travel can recover it. Use invalid_at for soft
    retirement, this for true mistakes / dedup cleanup.
    """
    try:
        deleted = await memory.delete_fact(user, fact_uuid, db)
    except memory.MemoryError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Fact {fact_uuid} not found")
