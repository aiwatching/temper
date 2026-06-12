"""Per-user memory snapshots — create / list / restore / prune + the
daily scheduler.

A snapshot is a MemoryBundleV1 (blocks + documents, and optionally
episodes) frozen at a point in time and stored in `memory_snapshots`.
Built on top of memory_export.build_bundle / apply_bundle.

Two creators:
  * the scheduler — `run_due_snapshots()` writes one `auto` snapshot
    per user whose last auto snapshot is older than the configured
    interval. Per-user "is it due?" logic makes it restart-safe: a
    bounce just means the next tick catches whoever's overdue.
  * the API — a user POSTs to take a `manual` snapshot (optionally
    including episodes).

Restore reuses apply_bundle (merge by default). NOTE: restoring a
snapshot that contains episodes re-extracts them through the LLM, which
costs tokens + time and is lossy. Blocks + documents restore exactly.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from memory_service.core.memory_export import apply_bundle, build_bundle
from memory_service.models import MemorySnapshot, User
from memory_service.schemas.memory_export import MemoryBundleV1

_logger = logging.getLogger(__name__)


# ---------- create ----------


async def create_snapshot(
    user: User,
    db: AsyncSession,
    *,
    kind: str = "manual",
    include_episodes: bool = False,
    note: str | None = None,
) -> MemorySnapshot:
    """Build a bundle for `user` and persist it as a snapshot row."""
    bundle = await build_bundle(user, db, include_episodes=include_episodes)
    payload = bundle.model_dump(mode="json")
    size = len(json.dumps(payload).encode("utf-8"))

    snap = MemorySnapshot(
        user_id=user.id,
        kind=kind,
        bundle=payload,
        created_at=datetime.now(UTC),
        include_episodes=include_episodes,
        blocks_count=len(bundle.blocks),
        documents_count=len(bundle.documents),
        episodes_count=len(bundle.episodes),
        size_bytes=size,
        note=note,
    )
    db.add(snap)
    await db.commit()
    await db.refresh(snap)
    return snap


# ---------- list / get ----------


# Columns for listing — everything except the (large) bundle blob.
_LIST_COLS = (
    MemorySnapshot.id,
    MemorySnapshot.user_id,
    MemorySnapshot.kind,
    MemorySnapshot.created_at,
    MemorySnapshot.include_episodes,
    MemorySnapshot.blocks_count,
    MemorySnapshot.documents_count,
    MemorySnapshot.episodes_count,
    MemorySnapshot.size_bytes,
    MemorySnapshot.note,
)


async def list_snapshots(
    user: User, db: AsyncSession, *, limit: int = 100,
) -> list[dict[str, Any]]:
    """Newest-first metadata list, WITHOUT the bundle payload."""
    stmt = (
        select(*_LIST_COLS)
        .where(MemorySnapshot.user_id == user.id)
        .order_by(MemorySnapshot.created_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [
        {
            "id": r.id,
            "kind": r.kind,
            "created_at": r.created_at,
            "include_episodes": r.include_episodes,
            "blocks_count": r.blocks_count,
            "documents_count": r.documents_count,
            "episodes_count": r.episodes_count,
            "size_bytes": r.size_bytes,
            "note": r.note,
        }
        for r in rows
    ]


async def get_snapshot(
    user: User, db: AsyncSession, snapshot_id: str,
) -> MemorySnapshot | None:
    """Fetch one snapshot owned by `user` (full bundle included).
    Returns None if missing or owned by someone else (callers map to
    404, never leaking another user's snapshot's existence)."""
    snap = await db.get(MemorySnapshot, snapshot_id)
    if snap is None or snap.user_id != user.id:
        return None
    return snap


# ---------- restore ----------


async def restore_snapshot(
    user: User,
    db: AsyncSession,
    snapshot_id: str,
    *,
    mode: str = "merge",
):
    """Replay a snapshot's bundle into the user's namespace.

    Returns (report, None) on success, (None, "not_found") when the
    snapshot doesn't belong to the user. Reuses apply_bundle, so
    `mode` is merge (default) | replace, and the episode caveat
    applies (re-extraction)."""
    snap = await get_snapshot(user, db, snapshot_id)
    if snap is None:
        return None, "not_found"
    bundle = MemoryBundleV1.model_validate(snap.bundle)
    report = await apply_bundle(user, bundle, db, mode=mode)
    return report, None


async def delete_snapshot(
    user: User, db: AsyncSession, snapshot_id: str,
) -> bool:
    snap = await get_snapshot(user, db, snapshot_id)
    if snap is None:
        return False
    await db.delete(snap)
    await db.commit()
    return True


# ---------- retention ----------


async def prune_auto_snapshots(
    user_id: str, db: AsyncSession, keep: int,
) -> int:
    """Delete all but the newest `keep` AUTO snapshots for a user.
    Manual snapshots are never touched. Returns count deleted."""
    keep_ids_stmt = (
        select(MemorySnapshot.id)
        .where(
            MemorySnapshot.user_id == user_id,
            MemorySnapshot.kind == "auto",
        )
        .order_by(MemorySnapshot.created_at.desc())
        .limit(keep)
    )
    keep_ids = [r[0] for r in (await db.execute(keep_ids_stmt)).all()]
    del_stmt = delete(MemorySnapshot).where(
        MemorySnapshot.user_id == user_id,
        MemorySnapshot.kind == "auto",
        MemorySnapshot.id.notin_(keep_ids) if keep_ids else True,  # noqa: E712
    )
    result = await db.execute(del_stmt)
    await db.commit()
    return result.rowcount or 0


# ---------- scheduler ----------


async def run_due_snapshots(db: AsyncSession) -> dict[str, int]:
    """Snapshot every user whose most recent auto snapshot is older
    than the configured interval (or who has none). Per-user due-check
    makes this idempotent + restart-safe. Returns a small tally."""
    from memory_service.config import get_settings

    settings = get_settings()
    if not settings.snapshot_enabled:
        return {"snapshotted": 0, "skipped": 0}

    interval = timedelta(hours=settings.snapshot_interval_hours)
    cutoff = datetime.now(UTC) - interval

    # Most-recent auto snapshot per user, in one query.
    last_auto = dict(
        (
            await db.execute(
                select(
                    MemorySnapshot.user_id,
                    func.max(MemorySnapshot.created_at),
                )
                .where(MemorySnapshot.kind == "auto")
                .group_by(MemorySnapshot.user_id)
            )
        ).all()
    )

    users = (
        await db.execute(select(User).where(User.is_active.is_(True)))
    ).scalars().all()

    snapshotted = 0
    skipped = 0
    for u in users:
        last = last_auto.get(u.id)
        # Postgres returns tz-aware; sqlite (tests) returns naive — treat
        # a naive timestamp as UTC so the comparison never raises.
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if last is not None and last > cutoff:
            skipped += 1
            continue
        try:
            await create_snapshot(u, db, kind="auto", include_episodes=False)
            await prune_auto_snapshots(u.id, db, settings.snapshot_retention)
            snapshotted += 1
        except Exception:
            _logger.exception("auto-snapshot failed for user %s", u.id)

    if snapshotted:
        _logger.info(
            "auto-snapshot pass: %d snapshotted, %d up-to-date",
            snapshotted, skipped,
        )
    return {"snapshotted": snapshotted, "skipped": skipped}
