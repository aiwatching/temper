"""/v1/me/snapshots — per-user point-in-time memory backups.

A snapshot freezes the caller's blocks + documents (and optionally
episodes) so they can roll their memory back to an earlier day. The
built-in scheduler takes a daily `auto` snapshot of every user;
these endpoints let a user take `manual` ones, list what exists, and
restore.

Auth: any authenticated user, scoped to THEIR OWN snapshots. There is
no cross-user access here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from memory_service.api.deps import CurrentUser, DBDep
from memory_service.core import snapshots
from memory_service.schemas.memory_export import ImportReport

router = APIRouter(prefix="/me/snapshots", tags=["memory-snapshots"])


class SnapshotMeta(BaseModel):
    id: str
    kind: Literal["auto", "manual"]
    created_at: datetime
    include_episodes: bool
    blocks_count: int
    documents_count: int
    episodes_count: int
    size_bytes: int
    note: str | None = None


class SnapshotListResponse(BaseModel):
    snapshots: list[SnapshotMeta]


class CreateSnapshotRequest(BaseModel):
    include_episodes: bool = Field(
        default=False,
        description=(
            "Include episode content in the snapshot. Default false: "
            "snapshots capture blocks + documents (exact restore). "
            "Episodes restore by re-extraction through the LLM — slower, "
            "costs tokens, and lossy — so only opt in when you "
            "specifically want them frozen."
        ),
    )
    note: str | None = Field(
        default=None, max_length=500,
        description="Optional label, e.g. 'before the big refactor'.",
    )


@router.post("", response_model=SnapshotMeta, status_code=status.HTTP_201_CREATED)
async def create_snapshot(
    payload: CreateSnapshotRequest,
    user: CurrentUser,
    db: DBDep,
) -> SnapshotMeta:
    """Take a manual snapshot of the caller's memory right now."""
    snap = await snapshots.create_snapshot(
        user, db,
        kind="manual",
        include_episodes=payload.include_episodes,
        note=payload.note,
    )
    return SnapshotMeta(
        id=snap.id,
        kind="manual",
        created_at=snap.created_at,
        include_episodes=snap.include_episodes,
        blocks_count=snap.blocks_count,
        documents_count=snap.documents_count,
        episodes_count=snap.episodes_count,
        size_bytes=snap.size_bytes,
        note=snap.note,
    )


@router.get("", response_model=SnapshotListResponse)
async def list_snapshots(
    user: CurrentUser,
    db: DBDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> SnapshotListResponse:
    """List the caller's snapshots, newest first (metadata only)."""
    rows = await snapshots.list_snapshots(user, db, limit=limit)
    return SnapshotListResponse(snapshots=[SnapshotMeta(**r) for r in rows])


@router.get("/{snapshot_id}", response_model=dict)
async def get_snapshot(
    snapshot_id: str,
    user: CurrentUser,
    db: DBDep,
) -> dict:
    """Download one snapshot's full bundle (the MemoryBundleV1 payload)."""
    snap = await snapshots.get_snapshot(user, db, snapshot_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return snap.bundle


@router.post("/{snapshot_id}/restore", response_model=ImportReport)
async def restore_snapshot(
    snapshot_id: str,
    user: CurrentUser,
    db: DBDep,
    mode: Annotated[
        Literal["merge", "replace"],
        Query(
            description=(
                "merge (default): upsert the snapshot's blocks / "
                "documents / episodes over current state. replace: wipe "
                "the caller's current data first, then load the snapshot "
                "(does NOT clear the FalkorDB graph — see /v1/me/import). "
                "Restoring episodes re-extracts them via the LLM."
            ),
        ),
    ] = "merge",
) -> ImportReport:
    """Roll the caller's memory back to a snapshot."""
    report, err = await snapshots.restore_snapshot(
        user, db, snapshot_id, mode=mode,
    )
    if err == "not_found":
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return report


@router.delete("/{snapshot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_snapshot(
    snapshot_id: str,
    user: CurrentUser,
    db: DBDep,
) -> None:
    if not await snapshots.delete_snapshot(user, db, snapshot_id):
        raise HTTPException(status_code=404, detail="Snapshot not found")
