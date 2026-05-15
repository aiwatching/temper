"""CRUD for memory_blocks — structured per-user key/value memory.

Where this fits vs Graphiti: see `models/memory_block.py` docstring and
`db/migrations/versions/0012_memory_blocks.py`. TL;DR: first-person
assertions (nickname, preferences, current focus) go here; third-party
world facts go to Graphiti.

Scopes:
  - "own"     → caller's API key agent_slug (or '*' if the key has none)
  - "global"  → '*' sentinel (visible to every agent under this user)
  - "both"    → list endpoints only; merge own + global with own
                shadowing global on key collision

All operations are user-scoped: a user can only read/write their own
blocks. No cross-user sharing — if two people want to share a block
they need to write it to each of their own user_ids.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memory_service.models import GLOBAL_AGENT_SLUG, MemoryBlock, User

_logger = logging.getLogger(__name__)

Scope = Literal["own", "global", "both"]


class BlockError(Exception):
    """Base for user-visible block errors. http_status is mapped by callers."""
    http_status: int = 500


class BlockNotFoundError(BlockError):
    http_status = 404


class BlockBadRequestError(BlockError):
    http_status = 400


# ---------- internal helpers ----------


def _caller_agent_slug(user: User) -> str:
    """Resolve the caller's effective agent_slug for scope='own'.

    API-key auth sets `_default_agent_slug` on the User object (see
    api/deps.py). Session auth (the human admin UI) doesn't, so we
    fall back to the global sentinel — admin actions on blocks
    default to global scope.
    """
    slug = getattr(user, "_default_agent_slug", None)
    if not slug:
        return GLOBAL_AGENT_SLUG
    return slug


def _scope_to_slug(scope: Scope, user: User, explicit: str | None) -> str:
    """Pick the agent_slug to read/write. Explicit `agent_slug` (when
    the caller passes one in the body) wins; otherwise scope decides."""
    if explicit:
        return explicit
    if scope == "global":
        return GLOBAL_AGENT_SLUG
    return _caller_agent_slug(user)


@dataclass
class BlockOut:
    """Wire-shape for a memory block. Pydantic schema in api/v1/blocks.py
    reflects this; keeping it as a dataclass here means core stays
    independent of FastAPI."""
    id: str
    user_id: str
    agent_slug: str
    block_key: str
    block_value: Any
    pinned: bool
    priority: int
    description: str | None
    created_at: datetime
    updated_at: datetime
    updated_by: str | None
    # "own" or "global" — set when the row is returned to the caller, so
    # the UI / agent can tell where the value came from in a merged list.
    scope: str


def _to_out(block: MemoryBlock, *, scope: str) -> BlockOut:
    return BlockOut(
        id=block.id,
        user_id=block.user_id,
        agent_slug=block.agent_slug,
        block_key=block.block_key,
        block_value=block.block_value,
        pinned=block.pinned,
        priority=block.priority,
        description=block.description,
        created_at=block.created_at,
        updated_at=block.updated_at,
        updated_by=block.updated_by,
        scope=scope,
    )


def _scope_for_row(block: MemoryBlock, caller_slug: str) -> str:
    if block.agent_slug == GLOBAL_AGENT_SLUG and caller_slug != GLOBAL_AGENT_SLUG:
        return "global"
    return "own"


def _deep_merge(target: Any, patch: Any) -> Any:
    """Right-biased deep merge. Only dicts are merged structurally; everything
    else (lists, scalars) is replaced. Matches the principle of least
    surprise for the JSONB use case — lists tend to be "the whole new
    list", not "add to the list"."""
    if isinstance(target, dict) and isinstance(patch, dict):
        out = dict(target)
        for k, v in patch.items():
            if k in out:
                out[k] = _deep_merge(out[k], v)
            else:
                out[k] = v
        return out
    return patch


# ---------- public API ----------


async def list_blocks(
    user: User,
    db: AsyncSession,
    *,
    scope: Scope = "both",
    pinned: bool | None = None,
    prefix: str | None = None,
) -> list[BlockOut]:
    """List blocks for the caller. scope='both' merges own + global,
    with own shadowing global on key collision."""
    caller_slug = _caller_agent_slug(user)

    stmt = select(MemoryBlock).where(MemoryBlock.user_id == user.id)
    if scope == "own":
        stmt = stmt.where(MemoryBlock.agent_slug == caller_slug)
    elif scope == "global":
        stmt = stmt.where(MemoryBlock.agent_slug == GLOBAL_AGENT_SLUG)
    elif scope == "both":
        # When caller is session-auth'd (no API key → no agent_slug),
        # they're the HUMAN — they should see every block they own
        # across every agent_slug, not just '*'. Otherwise scope='both'
        # collapses to IN ('*','*') and agent-written blocks vanish
        # from the admin UI even though the user is the owner.
        #
        # When caller has an agent_slug (API-key auth), scope='both'
        # returns own + global only, NOT other agents' blocks — agent
        # isolation is intentional there.
        if caller_slug != GLOBAL_AGENT_SLUG:
            stmt = stmt.where(
                MemoryBlock.agent_slug.in_([caller_slug, GLOBAL_AGENT_SLUG])
            )
        # else: human admin — no agent_slug filter, see everything they own.

    if pinned is not None:
        stmt = stmt.where(MemoryBlock.pinned.is_(pinned))
    if prefix:
        stmt = stmt.where(MemoryBlock.block_key.like(f"{prefix}%"))

    stmt = stmt.order_by(MemoryBlock.priority.desc(), MemoryBlock.block_key.asc())

    rows = (await db.execute(stmt)).scalars().all()

    if scope != "both" or caller_slug == GLOBAL_AGENT_SLUG:
        return [_to_out(b, scope=_scope_for_row(b, caller_slug)) for b in rows]

    # Merge own + global: per block_key, prefer own over global.
    by_key: dict[str, MemoryBlock] = {}
    for b in rows:
        existing = by_key.get(b.block_key)
        if existing is None:
            by_key[b.block_key] = b
            continue
        # Prefer the one matching caller_slug (own beats global).
        if existing.agent_slug == GLOBAL_AGENT_SLUG and b.agent_slug == caller_slug:
            by_key[b.block_key] = b
    out = list(by_key.values())
    # Re-sort after dedup (dict insertion preserved old order otherwise).
    out.sort(key=lambda b: (-b.priority, b.block_key))
    return [_to_out(b, scope=_scope_for_row(b, caller_slug)) for b in out]


async def get_block(
    user: User,
    db: AsyncSession,
    key: str,
    *,
    scope: Scope = "own",
) -> BlockOut | None:
    """Get one block. scope='own' falls back to 'global' if not found
    in own — matches the "shadow" semantics from list_blocks."""
    caller_slug = _caller_agent_slug(user)
    target = caller_slug if scope == "own" else GLOBAL_AGENT_SLUG

    block = await _fetch(db, user.id, target, key)
    if block is not None:
        return _to_out(block, scope=_scope_for_row(block, caller_slug))

    # Implicit fallback: scope=own missed → check global.
    if scope == "own" and caller_slug != GLOBAL_AGENT_SLUG:
        block = await _fetch(db, user.id, GLOBAL_AGENT_SLUG, key)
        if block is not None:
            return _to_out(block, scope="global")
    return None


async def upsert_block(
    user: User,
    db: AsyncSession,
    key: str,
    value: Any,
    *,
    scope: Scope = "own",
    pinned: bool | None = None,
    priority: int | None = None,
    description: str | None = None,
    agent_slug: str | None = None,
    updated_by: str | None = None,
) -> BlockOut:
    """Create or replace a block. value REPLACES whatever was there.
    For partial JSON merge use `patch_block`."""
    if not key.strip():
        raise BlockBadRequestError("block_key must be non-empty")

    target = _scope_to_slug(scope if scope != "both" else "own", user, agent_slug)
    caller_slug = _caller_agent_slug(user)

    block = await _fetch(db, user.id, target, key)
    if block is None:
        block = MemoryBlock(
            user_id=user.id,
            block_key=key,
            block_value=value,
            agent_slug=target,
        )
        if pinned is not None:
            block.pinned = pinned
        if priority is not None:
            block.priority = priority
        if description is not None:
            block.description = description
        if updated_by is not None:
            block.updated_by = updated_by
        db.add(block)
    else:
        block.block_value = value
        if pinned is not None:
            block.pinned = pinned
        if priority is not None:
            block.priority = priority
        if description is not None:
            block.description = description
        if updated_by is not None:
            block.updated_by = updated_by

    await db.flush()
    await db.commit()
    await db.refresh(block)
    return _to_out(block, scope=_scope_for_row(block, caller_slug))


async def patch_block(
    user: User,
    db: AsyncSession,
    key: str,
    *,
    value: Any = None,             # if provided, deep-merged into existing
    scope: Scope = "own",
    pinned: bool | None = None,
    priority: int | None = None,
    description: str | None = None,
    agent_slug: str | None = None,
    updated_by: str | None = None,
) -> BlockOut:
    """Partial update. JSONB `value` is deep-merged; other fields replace."""
    target = _scope_to_slug(scope if scope != "both" else "own", user, agent_slug)
    block = await _fetch(db, user.id, target, key)
    if block is None:
        raise BlockNotFoundError(
            f"block '{key}' not found in scope={scope} for user {user.id}"
        )

    if value is not None:
        block.block_value = _deep_merge(block.block_value, value)
    if pinned is not None:
        block.pinned = pinned
    if priority is not None:
        block.priority = priority
    if description is not None:
        block.description = description
    if updated_by is not None:
        block.updated_by = updated_by

    await db.flush()
    await db.commit()
    await db.refresh(block)
    caller_slug = _caller_agent_slug(user)
    return _to_out(block, scope=_scope_for_row(block, caller_slug))


async def delete_block(
    user: User,
    db: AsyncSession,
    key: str,
    *,
    scope: Scope = "own",
    agent_slug: str | None = None,
) -> bool:
    target = _scope_to_slug(scope if scope != "both" else "own", user, agent_slug)
    block = await _fetch(db, user.id, target, key)
    if block is None:
        return False
    await db.delete(block)
    await db.commit()
    return True


async def _fetch(
    db: AsyncSession, user_id: str, agent_slug: str, key: str
) -> MemoryBlock | None:
    stmt = select(MemoryBlock).where(
        MemoryBlock.user_id == user_id,
        MemoryBlock.agent_slug == agent_slug,
        MemoryBlock.block_key == key,
    )
    return (await db.execute(stmt)).scalar_one_or_none()
