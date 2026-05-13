"""/v1/episodes — write / list / get / delete memory episodes."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select

from memory_service.api.deps import CurrentUser, DBDep
from memory_service.core import memory
from memory_service.models import APIKey
from memory_service.schemas.episode import (
    BulkEpisodesRequest,
    BulkEpisodesResponse,
    CreateEpisodeRequest,
    CreateEpisodeResponse,
    EntityOut,
    EpisodeDetailResponse,
    EpisodeListResponse,
    EpisodeStatusResponse,
    EpisodeSummary,
    FactOut,
)
from memory_service.models import EpisodeMetadata

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


@router.post("", response_model=CreateEpisodeResponse)
async def create_episode(
    payload: CreateEpisodeRequest,
    user: CurrentUser,
    db: DBDep,
    response: Response,
    async_extract: Annotated[
        bool,
        Query(
            description="Return 202 immediately and run Graphiti extraction "
            "in a background task. Poll GET /v1/episodes/{id}/status. "
            "Useful when you want to write fast and don't need facts back "
            "in the same call."
        ),
    ] = False,
):
    agent_name = await _agent_name_for(user.id, db)
    req = memory.WriteRequest(
        namespace=payload.namespace or "",
        content=payload.content,
        source_type=payload.source_type,
        source_description=payload.source_description or "",
        reference_time=payload.reference_time,
        tags=payload.tags or [],
        saga=payload.saga,
    )
    try:
        result = await memory.add_episode(
            user, agent_name, req, db, async_extract=async_extract
        )
    except memory.MemoryError as exc:
        raise _to_http(exc) from exc

    response.status_code = (
        status.HTTP_202_ACCEPTED if async_extract else status.HTTP_201_CREATED
    )

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


@router.post(
    "/bulk",
    status_code=status.HTTP_201_CREATED,
    response_model=BulkEpisodesResponse,
)
async def create_episodes_bulk(
    payload: BulkEpisodesRequest,
    user: CurrentUser,
    db: DBDep,
) -> BulkEpisodesResponse:
    """Write up to 200 episodes in one Graphiti pass.

    Same write semantics as POST /v1/episodes for each item, but extraction
    runs once over the whole batch — meaningfully faster than looping
    POST /episodes when importing chat history or logs. All items land
    in the same namespace; pass different namespaces in separate calls.
    """
    agent_name = await _agent_name_for(user.id, db)
    items = [
        memory.BulkWriteItem(
            content=item.content,
            source_type=item.source_type,
            source_description=item.source_description or "",
            reference_time=item.reference_time,
            tags=item.tags or [],
        )
        for item in payload.items
    ]
    try:
        result = await memory.add_episodes_bulk(
            user, agent_name, payload.namespace, items, db, saga=payload.saga,
        )
    except memory.MemoryError as exc:
        raise _to_http(exc) from exc

    return BulkEpisodesResponse(
        episode_ids=result.episode_ids,
        namespace=result.namespace,
        total_entities=result.total_entities,
        total_facts=result.total_facts,
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


@router.get("/{episode_id}/status", response_model=EpisodeStatusResponse)
async def get_extraction_status(
    episode_id: str, user: CurrentUser, db: DBDep
) -> EpisodeStatusResponse:
    """Poll the extraction status after POST with `?async_extract=true`.

    Cheap: hits SQL only, doesn't touch FalkorDB. 404 if the episode
    doesn't exist or you can't read its namespace (same posture as the
    rest of the API)."""
    meta = await db.get(EpisodeMetadata, episode_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Episode {episode_id} not found")
    from memory_service.core.namespaces import can_read, parse

    if not await can_read(user, parse(meta.namespace), db):
        raise HTTPException(status_code=404, detail=f"Episode {episode_id} not found")
    return EpisodeStatusResponse(
        episode_id=meta.id,
        extraction_status=meta.extraction_status,  # type: ignore[arg-type]
        extraction_error=meta.extraction_error,
    )


@router.delete("/{episode_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_episode(episode_id: str, user: CurrentUser, db: DBDep) -> None:
    try:
        await memory.delete_episode(user, episode_id, db)
    except memory.MemoryError as exc:
        raise _to_http(exc) from exc
