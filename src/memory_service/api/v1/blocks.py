"""/v1/memory/blocks — structured key/value memory blocks.

See core/blocks.py for the design rationale (TL;DR: this is the storage
for first-person assertions that Graphiti's entity extraction is bad
at — nicknames, preferences, daily routines, current focus).

Five endpoints (GET list, GET one, PUT upsert, PATCH merge, DELETE).
All require auth; scoped to the calling user. The caller's API key
agent_slug picks the default "own" scope; pass `scope=global` to read
or write the cross-agent slot.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from memory_service.api.deps import CurrentUser, DBDep
from memory_service.core import blocks

router = APIRouter(prefix="/memory", tags=["memory-blocks"])

Scope = Literal["own", "global", "both"]


class BlockResponse(BaseModel):
    id: str
    user_id: str
    agent_slug: str
    block_key: str
    block_value: Any
    pinned: bool
    priority: int
    description: str | None = None
    created_at: datetime
    updated_at: datetime
    updated_by: str | None = None
    scope: Literal["own", "global"]


class BlockListResponse(BaseModel):
    blocks: list[BlockResponse]


class UpsertBlockRequest(BaseModel):
    value: Any = Field(..., description="Block value. Replaces existing on PUT; deep-merged on PATCH.")
    pinned: bool | None = Field(
        default=None,
        description=(
            "If true, the block is auto-injected into the agent's "
            "system prompt every turn. Use sparingly — pinned content "
            "spends prompt tokens on every call."
        ),
    )
    priority: int | None = Field(
        default=None,
        description="Higher priority = shown first when multiple pinned blocks render.",
    )
    description: str | None = Field(
        default=None,
        description="One-liner shown to the agent on read so the block self-documents.",
    )
    scope: Scope | None = Field(
        default=None,
        description=(
            "'own' (default) = caller's agent_slug; 'global' = '*' "
            "sentinel (every agent under this user sees it). "
            "'both' is list-only."
        ),
    )
    agent_slug: str | None = Field(
        default=None,
        description="Explicit agent_slug override. Wins over `scope` when set.",
    )


class PatchBlockRequest(BaseModel):
    value: Any | None = Field(
        default=None,
        description="JSONB deep-merge target. Leave unset to update only metadata.",
    )
    pinned: bool | None = None
    priority: int | None = None
    description: str | None = None
    scope: Scope | None = None
    agent_slug: str | None = None


def _updated_by(user: CurrentUser) -> str:
    slug = getattr(user, "_default_agent_slug", None)
    return f"agent:{slug}" if slug else f"user:{user.email}"


@router.get("/blocks", response_model=BlockListResponse)
async def list_blocks(
    user: CurrentUser,
    db: DBDep,
    scope: Annotated[Scope, Query(description="own | global | both (default: both — merge)")] = "both",
    pinned: Annotated[bool | None, Query(description="Filter by pinned status")] = None,
    prefix: Annotated[str | None, Query(description="Only keys starting with this prefix")] = None,
) -> BlockListResponse:
    try:
        out = await blocks.list_blocks(
            user, db, scope=scope, pinned=pinned, prefix=prefix,
        )
    except blocks.BlockError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    return BlockListResponse(blocks=[BlockResponse(**b.__dict__) for b in out])


@router.get("/blocks/{key:path}", response_model=BlockResponse)
async def get_block(
    key: str,
    user: CurrentUser,
    db: DBDep,
    scope: Annotated[Scope, Query()] = "own",
) -> BlockResponse:
    try:
        out = await blocks.get_block(user, db, key, scope=scope if scope != "both" else "own")
    except blocks.BlockError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    if out is None:
        raise HTTPException(status_code=404, detail=f"Block '{key}' not found")
    return BlockResponse(**out.__dict__)


@router.put("/blocks/{key:path}", response_model=BlockResponse)
async def put_block(
    key: str,
    payload: UpsertBlockRequest,
    user: CurrentUser,
    db: DBDep,
) -> BlockResponse:
    try:
        out = await blocks.upsert_block(
            user, db, key,
            value=payload.value,
            scope=payload.scope or "own",
            pinned=payload.pinned,
            priority=payload.priority,
            description=payload.description,
            agent_slug=payload.agent_slug,
            updated_by=_updated_by(user),
        )
    except blocks.BlockError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    return BlockResponse(**out.__dict__)


@router.patch("/blocks/{key:path}", response_model=BlockResponse)
async def patch_block(
    key: str,
    payload: PatchBlockRequest,
    user: CurrentUser,
    db: DBDep,
) -> BlockResponse:
    try:
        out = await blocks.patch_block(
            user, db, key,
            value=payload.value,
            scope=payload.scope or "own",
            pinned=payload.pinned,
            priority=payload.priority,
            description=payload.description,
            agent_slug=payload.agent_slug,
            updated_by=_updated_by(user),
        )
    except blocks.BlockError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    return BlockResponse(**out.__dict__)


@router.delete("/blocks/{key:path}", status_code=204)
async def delete_block(
    key: str,
    user: CurrentUser,
    db: DBDep,
    scope: Annotated[Scope, Query()] = "own",
    agent_slug: Annotated[str | None, Query()] = None,
) -> None:
    try:
        deleted = await blocks.delete_block(
            user, db, key,
            scope=scope if scope != "both" else "own",
            agent_slug=agent_slug,
        )
    except blocks.BlockError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Block '{key}' not found")
