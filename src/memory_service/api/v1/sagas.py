"""/v1/sagas — list and inspect Saga groupings.

Sagas are Graphiti's way of marking that a chain of episodes is one
logical conversation/document/event. Create them by passing `saga:
"<name>"` on POST /v1/episodes or /v1/episodes/bulk; this surface is
read-only inspection.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from memory_service.api.deps import CurrentUser, DBDep
from memory_service.core import memory

router = APIRouter(prefix="/sagas", tags=["sagas"])


class SagaSummary(BaseModel):
    uuid: str
    name: str
    summary: str | None = None
    created_at: datetime | None = None
    episode_count: int


class SagaListResponse(BaseModel):
    namespace: str
    sagas: list[SagaSummary]


class SagaEpisode(BaseModel):
    uuid: str
    content: str | None
    created_at: datetime | None = None


class SagaDetail(BaseModel):
    uuid: str
    name: str
    summary: str | None = None
    created_at: datetime | None = None
    first_episode_uuid: str | None = None
    last_episode_uuid: str | None = None


class SagaDetailResponse(BaseModel):
    namespace: str
    saga: SagaDetail
    episodes: list[SagaEpisode]


@router.get("", response_model=SagaListResponse)
async def list_sagas(
    user: CurrentUser,
    db: DBDep,
    namespace: Annotated[str | None, Query()] = None,
) -> SagaListResponse:
    try:
        data = await memory.list_sagas(user, namespace, db)
    except memory.MemoryError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    return SagaListResponse(**data)


@router.get("/{name_or_uuid}", response_model=SagaDetailResponse)
async def get_saga(
    name_or_uuid: str,
    user: CurrentUser,
    db: DBDep,
    namespace: Annotated[str | None, Query()] = None,
) -> SagaDetailResponse:
    try:
        data = await memory.get_saga(user, namespace, name_or_uuid, db)
    except memory.MemoryError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    if data is None:
        raise HTTPException(
            status_code=404, detail=f"Saga {name_or_uuid!r} not found"
        )
    return SagaDetailResponse(**data)
