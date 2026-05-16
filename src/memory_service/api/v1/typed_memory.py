"""/v1/memory — typed memory endpoints.

Sits above the raw block / episode primitives. See
core/typed_memory.py for the routing logic and block_key conventions.

Endpoints in this module:

  POST   /v1/memory/tasks                 add_task
  GET    /v1/memory/tasks                 list_tasks (?status=todo|doing|blocked)
  PATCH  /v1/memory/tasks/{task_id}       update_task
  POST   /v1/memory/tasks/{task_id}/complete  complete_task (block + episode)

  GET    /v1/memory/focus                 get_focus
  PUT    /v1/memory/focus                 set_focus (block + episode on change)

  GET    /v1/memory/preferences           list_preferences
  PUT    /v1/memory/preferences/{key}     set_preference (global scope)

  POST   /v1/memory/events                note_event (thin pass-through)

  GET    /v1/memory/turn_context          turn_context bundle (read all)

All routes auth via X-API-Key (same as the rest of /v1) and inherit
the caller's agent_slug for scope decisions. Preferences are written
to the user's global scope so other agents can read them — see
core/typed_memory.set_preference.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from memory_service.api.deps import CurrentUser, DBDep
from memory_service.core import typed_memory as tm
from memory_service.schemas.typed_memory import (
    CompleteTaskRequest,
    CreateTaskRequest,
    FocusResponse,
    NoteEventRequest,
    NoteEventResponse,
    PinnedBlockOut,
    PreferenceItem,
    PreferenceListResponse,
    RecalledEpisodeOut,
    SetFocusRequest,
    SetPreferenceRequest,
    TaskCompleteResponse,
    TaskItem,
    TaskListResponse,
    TurnContextResponse,
    UpdateTaskRequest,
)

router = APIRouter(prefix="/memory", tags=["memory-typed"])


def _to_http(exc: tm.TypedMemoryError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=str(exc))


def _task_to_schema(t: tm.TaskItem) -> TaskItem:
    return TaskItem(
        id=t.id, title=t.title, status=t.status, priority=t.priority,
        notes=t.notes, created_at=t.created_at, updated_at=t.updated_at,
    )


# --- tasks -------------------------------------------------------------------


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    user: CurrentUser,
    db: DBDep,
    status: Annotated[str | None, Query(description="todo | doing | blocked")] = None,
) -> TaskListResponse:
    try:
        items = await tm.list_tasks(user, db, status=status)
    except tm.TypedMemoryError as exc:
        raise _to_http(exc) from exc
    return TaskListResponse(tasks=[_task_to_schema(t) for t in items])


@router.post("/tasks", response_model=TaskItem, status_code=201)
async def add_task(
    payload: CreateTaskRequest,
    user: CurrentUser,
    db: DBDep,
) -> TaskItem:
    try:
        item = await tm.add_task(
            user, db,
            title=payload.title, status=payload.status,
            priority=payload.priority, notes=payload.notes,
        )
    except tm.TypedMemoryError as exc:
        raise _to_http(exc) from exc
    return _task_to_schema(item)


@router.patch("/tasks/{task_id}", response_model=TaskItem)
async def update_task(
    task_id: str,
    payload: UpdateTaskRequest,
    user: CurrentUser,
    db: DBDep,
) -> TaskItem:
    try:
        item = await tm.update_task(
            user, db, task_id,
            title=payload.title, status=payload.status,
            priority=payload.priority, notes=payload.notes,
        )
    except tm.TypedMemoryError as exc:
        raise _to_http(exc) from exc
    return _task_to_schema(item)


@router.post("/tasks/{task_id}/complete", response_model=TaskCompleteResponse)
async def complete_task(
    task_id: str,
    payload: CompleteTaskRequest,
    user: CurrentUser,
    db: DBDep,
) -> TaskCompleteResponse:
    try:
        result = await tm.complete_task(user, db, task_id, summary=payload.summary)
    except tm.TypedMemoryError as exc:
        raise _to_http(exc) from exc
    except Exception as exc:  # graphiti propagation
        # Translate memory backend issues so the agent gets a useful code.
        from memory_service.core.memory import MemoryError as _Mem
        if isinstance(exc, _Mem):
            raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
        raise
    return TaskCompleteResponse(
        completed=_task_to_schema(result.item),
        episode_id=result.episode_id,
    )


# --- focus -------------------------------------------------------------------


@router.get("/focus", response_model=FocusResponse)
async def get_focus(user: CurrentUser, db: DBDep) -> FocusResponse:
    f = await tm.get_focus(user, db)
    return FocusResponse(value=f.value, updated_at=f.updated_at, episode_id=None)


@router.put("/focus", response_model=FocusResponse)
async def set_focus(
    payload: SetFocusRequest,
    user: CurrentUser,
    db: DBDep,
) -> FocusResponse:
    try:
        result = await tm.set_focus(user, db, value=payload.value, note=payload.note)
    except tm.TypedMemoryError as exc:
        raise _to_http(exc) from exc
    return FocusResponse(
        value=result.value, updated_at=result.updated_at,
        episode_id=result.episode_id or None,
    )


# --- preferences -------------------------------------------------------------


@router.get("/preferences", response_model=PreferenceListResponse)
async def list_preferences(
    user: CurrentUser, db: DBDep,
) -> PreferenceListResponse:
    items = await tm.list_preferences(user, db)
    return PreferenceListResponse(
        preferences=[
            PreferenceItem(
                key=p.key, value=p.value,
                description=p.description, updated_at=p.updated_at,
            )
            for p in items
        ]
    )


@router.put("/preferences/{key}", response_model=PreferenceItem)
async def set_preference(
    key: str,
    payload: SetPreferenceRequest,
    user: CurrentUser,
    db: DBDep,
) -> PreferenceItem:
    try:
        item = await tm.set_preference(
            user, db, key=key, value=payload.value, description=payload.description,
        )
    except tm.TypedMemoryError as exc:
        raise _to_http(exc) from exc
    return PreferenceItem(
        key=item.key, value=item.value,
        description=item.description, updated_at=item.updated_at,
    )


# --- events ------------------------------------------------------------------


@router.post("/events", response_model=NoteEventResponse, status_code=201)
async def note_event(
    payload: NoteEventRequest,
    user: CurrentUser,
    db: DBDep,
) -> NoteEventResponse:
    from memory_service.core.memory import MemoryError as _Mem
    try:
        result = await tm.note_event(
            user, db,
            content=payload.content, namespace=payload.namespace,
            reference_time=payload.reference_time, tags=payload.tags,
            saga=payload.saga,
        )
    except _Mem as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    return NoteEventResponse(
        episode_id=result.episode_id, namespace=result.namespace,
        created_at=result.created_at,
    )


# --- turn_context ------------------------------------------------------------


@router.get("/turn_context", response_model=TurnContextResponse)
async def get_turn_context(
    user: CurrentUser,
    db: DBDep,
    query: Annotated[
        str | None,
        Query(
            description=(
                "User's latest message — drives the recall RRF. "
                "Omit to skip recall and return only pinned/structured state."
            ),
            max_length=1000,
        ),
    ] = None,
    recall_limit: Annotated[int, Query(ge=1, le=30)] = 10,
    namespaces: Annotated[
        str | None,
        Query(description="Comma-separated override; defaults to agent's own + user:me"),
    ] = None,
) -> TurnContextResponse:
    ns_list = (
        [n.strip() for n in namespaces.split(",") if n.strip()]
        if namespaces else None
    )
    ctx = await tm.build_turn_context(
        user, db, query=query, recall_limit=recall_limit, namespaces=ns_list,
    )
    return TurnContextResponse(
        active_tasks=[_task_to_schema(t) for t in ctx.active_tasks],
        current_focus=ctx.current_focus,
        preferences=ctx.preferences,
        pinned_blocks=[
            PinnedBlockOut(
                key=p.key, value=p.value, priority=p.priority,
                description=p.description, scope=p.scope,
            )
            for p in ctx.pinned_blocks
        ],
        recalled_episodes=[
            RecalledEpisodeOut(
                episode_id=r.episode_id, namespace=r.namespace, fact=r.fact,
                score=r.score, valid_at=r.valid_at, invalid_at=r.invalid_at,
            )
            for r in ctx.recalled_episodes
        ],
        namespaces_searched=ctx.namespaces_searched,
    )
