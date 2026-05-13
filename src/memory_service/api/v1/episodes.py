"""/v1/episodes — write / list / get / delete memory episodes."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from memory_service.api.deps import CurrentUser, DBDep
from memory_service.core import memory
from memory_service.models import APIKey
from memory_service.schemas.episode import (
    CreateEpisodeRequest,
    CreateEpisodeResponse,
    EntityOut,
    EpisodeDetailResponse,
    EpisodeListResponse,
    EpisodeSummary,
    FactOut,
)

router = APIRouter(prefix="/episodes", tags=["episodes"])


def _to_http(exc: memory.MemoryError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=str(exc))


async def _agent_name_for(user_id: str, db) -> str:  # type: ignore[no-untyped-def]
    """Best-effort agent name from the most-recently-used non-revoked key."""
    stmt = (
        select(APIKey.agent_name)
        .where(APIKey.user_id == user_id, APIKey.revoked.is_(False))
        .order_by(APIKey.last_used_at.desc().nullslast(), APIKey.created_at.desc())
        .limit(1)
    )
    name = (await db.execute(stmt)).scalar_one_or_none()
    return name or "web-console"


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CreateEpisodeResponse)
async def create_episode(
    payload: CreateEpisodeRequest,
    user: CurrentUser,
    db: DBDep,
) -> CreateEpisodeResponse:
    agent_name = await _agent_name_for(user.id, db)
    req = memory.WriteRequest(
        namespace=payload.namespace or "",
        content=payload.content,
        source_type=payload.source_type,
        source_description=payload.source_description or "",
        reference_time=payload.reference_time,
        tags=payload.tags or [],
    )
    try:
        result = await memory.add_episode(user, agent_name, req, db)
    except memory.MemoryError as exc:
        raise _to_http(exc) from exc

    return CreateEpisodeResponse(
        episode_id=result.episode_id,
        namespace=result.namespace,
        extracted_entities=[EntityOut(**e.__dict__) for e in result.extracted_entities],
        extracted_facts=[
            FactOut(
                uuid=f.uuid,
                fact=f.fact,
                source_entity_uuid=f.source_entity_uuid,
                target_entity_uuid=f.target_entity_uuid,
                valid_at=f.valid_at,
                invalid_at=f.invalid_at,
            )
            for f in result.extracted_facts
        ],
        created_at=result.created_at,
    )


@router.get("", response_model=EpisodeListResponse)
async def list_episodes(
    user: CurrentUser,
    db: DBDep,
    namespace: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    before: Annotated[datetime | None, Query()] = None,
) -> EpisodeListResponse:
    try:
        rows, next_cursor = await memory.list_episodes(user, namespace, limit, before, db)
    except memory.MemoryError as exc:
        raise _to_http(exc) from exc
    return EpisodeListResponse(
        episodes=[
            EpisodeSummary(
                episode_id=r.id,
                namespace=r.namespace,
                created_by_user_id=r.created_by_user_id,
                created_by_agent=r.created_by_agent,
                source_type=r.source_type,
                tags=r.tags or [],
                reference_time=r.reference_time,
                created_at=r.created_at,
            )
            for r in rows
        ],
        next_cursor=next_cursor,
    )


@router.get("/{episode_id}", response_model=EpisodeDetailResponse)
async def get_episode(episode_id: str, user: CurrentUser, db: DBDep) -> EpisodeDetailResponse:
    try:
        data = await memory.get_episode(user, episode_id, db)
    except memory.MemoryError as exc:
        raise _to_http(exc) from exc
    return EpisodeDetailResponse(**data)


@router.delete("/{episode_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_episode(episode_id: str, user: CurrentUser, db: DBDep) -> None:
    try:
        await memory.delete_episode(user, episode_id, db)
    except memory.MemoryError as exc:
        raise _to_http(exc) from exc
