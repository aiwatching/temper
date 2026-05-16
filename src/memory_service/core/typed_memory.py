"""Typed memory operations — the routing layer between agents and storage.

This module exists because asking the model to remember "tasks go in
block state.active_tasks, focus goes in block state.current_focus,
preferences go in block preferences.<key> with global scope, events
go in graphiti episode in default namespace" is the wrong shape of
problem: by the time the agent has it right, every other agent has
to relearn it. Move that knowledge into TEMPER once, expose typed
entry points, and let agents pick intent by picking a function name.

What lives here:

  * block_key conventions (CANONICAL — agents must not hardcode these)
  * task list manipulation against the JSONB array in state.active_tasks
  * atomic cross-storage ops (task_complete = block update + episode)
  * turn_context bundler (one call returns everything always-on)

What does NOT live here:

  * permission / scope plumbing — that's still core/blocks + core/memory
  * graphiti specifics — go through core/memory.add_episode / search
  * pydantic schemas — see schemas/typed_memory.py

Block key conventions:

  state.active_tasks      list[TaskItem]      own,    pinned, p=100
  state.current_focus     str                 own,    pinned, p=100
  preferences.<key>       any                 global, pinned, p=90

These defaults are written on first touch; subsequent writes don't
overwrite pinned/priority unless the caller asks. The agent never
needs to know "I should pin this" — typed_memory does it.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from memory_service.core import blocks as block_ops
from memory_service.core import memory as episode_ops
from memory_service.core.namespaces import default_namespace_for
from memory_service.models import User

_logger = logging.getLogger(__name__)


# --- canonical block_key constants -------------------------------------------

TASKS_KEY = "state.active_tasks"
FOCUS_KEY = "state.current_focus"
PREF_PREFIX = "preferences."

_TASKS_DEFAULT_DESCRIPTION = (
    "Active tasks the agent is currently working on. "
    "Each item: {id, title, status, priority, notes?, created_at, updated_at}. "
    "Completed tasks are moved out — they live as graphiti episodes."
)
_FOCUS_DEFAULT_DESCRIPTION = (
    "What the agent is currently focused on (a project, saga, or topic). "
    "Reads here when the user asks 'what are you working on'. "
    "Focus changes are also appended to graphiti for history."
)
_PREF_DEFAULT_DESCRIPTION_FMT = "User preference: {key}"


# --- typed errors ------------------------------------------------------------


class TypedMemoryError(Exception):
    http_status: int = 500


class TaskNotFoundError(TypedMemoryError):
    http_status = 404


class TypedMemoryBadRequestError(TypedMemoryError):
    http_status = 400


# --- helpers -----------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _new_task_id() -> str:
    # Short enough to be human-quotable, long enough to avoid collisions
    # in a single user's active list (which is bounded ~20 by design).
    return uuid.uuid4().hex[:8]


def _updated_by(user: User) -> str:
    slug = getattr(user, "_default_agent_slug", None)
    return f"agent:{slug}" if slug else f"user:{user.email}"


def _coerce_task_list(value: Any) -> list[dict[str, Any]]:
    """Defensive: the JSONB value should always be a list, but a manual
    edit through /admin or a botched migration could leave it as null
    or some other shape. Treat anything non-list as empty."""
    if isinstance(value, list):
        # Filter out non-dicts in case someone wrote raw strings in.
        return [t for t in value if isinstance(t, dict)]
    return []


async def _read_task_list(user: User, db: AsyncSession) -> list[dict[str, Any]]:
    block = await block_ops.get_block(user, db, TASKS_KEY, scope="own")
    if block is None:
        return []
    return _coerce_task_list(block.block_value)


async def _write_task_list(
    user: User,
    db: AsyncSession,
    tasks: list[dict[str, Any]],
) -> None:
    await block_ops.upsert_block(
        user, db, TASKS_KEY,
        value=tasks,
        scope="own",
        pinned=True,
        priority=100,
        description=_TASKS_DEFAULT_DESCRIPTION,
        updated_by=_updated_by(user),
    )


# --- tasks -------------------------------------------------------------------


@dataclass
class TaskItem:
    id: str
    title: str
    status: str
    priority: int
    notes: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TaskItem:
        return cls(
            id=str(d.get("id", "")),
            title=str(d.get("title", "")),
            status=str(d.get("status", "todo")),
            priority=int(d.get("priority", 50)),
            notes=d.get("notes"),
            created_at=_parse_dt(d.get("created_at")),
            updated_at=_parse_dt(d.get("updated_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "priority": self.priority,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
        if self.notes is not None:
            out["notes"] = self.notes
        return out


def _parse_dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            pass
    return _now()


async def list_tasks(
    user: User,
    db: AsyncSession,
    *,
    status: str | None = None,
) -> list[TaskItem]:
    raw = await _read_task_list(user, db)
    items = [TaskItem.from_dict(t) for t in raw]
    if status:
        items = [t for t in items if t.status == status]
    # Higher priority first, then most-recently-updated first.
    items.sort(key=lambda t: (-t.priority, -t.updated_at.timestamp()))
    return items


async def add_task(
    user: User,
    db: AsyncSession,
    *,
    title: str,
    status: str = "todo",
    priority: int = 50,
    notes: str | None = None,
) -> TaskItem:
    if not title.strip():
        raise TypedMemoryBadRequestError("task title must be non-empty")
    if status not in ("todo", "doing", "blocked"):
        raise TypedMemoryBadRequestError(
            f"task status must be one of: todo, doing, blocked. Got: {status!r}"
        )

    now = _now()
    item = TaskItem(
        id=_new_task_id(),
        title=title.strip(),
        status=status,
        priority=priority,
        notes=notes,
        created_at=now,
        updated_at=now,
    )
    current = await _read_task_list(user, db)
    current.append(item.to_dict())
    await _write_task_list(user, db, current)
    return item


async def update_task(
    user: User,
    db: AsyncSession,
    task_id: str,
    *,
    title: str | None = None,
    status: str | None = None,
    priority: int | None = None,
    notes: str | None = None,
) -> TaskItem:
    if status is not None and status not in ("todo", "doing", "blocked"):
        raise TypedMemoryBadRequestError(
            f"task status must be one of: todo, doing, blocked. Got: {status!r}"
        )

    current = await _read_task_list(user, db)
    for t in current:
        if t.get("id") == task_id:
            if title is not None:
                t["title"] = title.strip()
            if status is not None:
                t["status"] = status
            if priority is not None:
                t["priority"] = priority
            if notes is not None:
                t["notes"] = notes
            t["updated_at"] = _now().isoformat()
            await _write_task_list(user, db, current)
            return TaskItem.from_dict(t)
    raise TaskNotFoundError(f"task {task_id!r} not found")


@dataclass
class TaskCompleted:
    item: TaskItem
    episode_id: str


async def complete_task(
    user: User,
    db: AsyncSession,
    task_id: str,
    *,
    summary: str | None = None,
) -> TaskCompleted:
    """Move a task out of the active list, append a graphiti episode.

    Two writes (block + episode) — if the episode write fails AFTER we
    pop the task from the block, the task is gone from active but
    nothing recorded its completion. The risk is small (the episode
    write is the last step and writes to the same Postgres tx) but
    callers should treat "missing episode_id" as recoverable: the task
    is still completed, just unlogged.
    """
    current = await _read_task_list(user, db)
    matched: dict[str, Any] | None = None
    kept: list[dict[str, Any]] = []
    for t in current:
        if t.get("id") == task_id and matched is None:
            matched = t
        else:
            kept.append(t)
    if matched is None:
        raise TaskNotFoundError(f"task {task_id!r} not found")

    matched["status"] = "done"
    matched["updated_at"] = _now().isoformat()

    # Block first — make the user-visible state right immediately.
    await _write_task_list(user, db, kept)

    # Episode second — best-effort log. If graphiti errors, the
    # MemoryError propagates and the API returns 5xx, but the block
    # write above is already committed. Worth it: stale block ("looks
    # done but not in graphiti") would be a worse failure mode.
    title = matched.get("title", "(untitled task)")
    line = summary or title
    content = (
        f"Completed task '{title}' (id={task_id}). {line}"
        if summary else
        f"Completed task '{title}' (id={task_id})."
    )
    agent_name = getattr(user, "_default_agent_slug", None) or "typed-memory"
    write_req = episode_ops.WriteRequest(
        namespace="",  # caller's default
        content=content,
        source_type="text",
        source_description=f"task-complete:{task_id}",
        reference_time=_now(),
        tags=["task-complete"],
    )
    result = await episode_ops.add_episode(user, agent_name, write_req, db)
    return TaskCompleted(item=TaskItem.from_dict(matched), episode_id=result.episode_id)


# --- focus -------------------------------------------------------------------


@dataclass
class Focus:
    value: str | None
    updated_at: datetime | None


async def get_focus(user: User, db: AsyncSession) -> Focus:
    block = await block_ops.get_block(user, db, FOCUS_KEY, scope="own")
    if block is None:
        return Focus(value=None, updated_at=None)
    return Focus(value=str(block.block_value), updated_at=block.updated_at)


@dataclass
class FocusSet:
    value: str
    updated_at: datetime
    episode_id: str


async def set_focus(
    user: User,
    db: AsyncSession,
    *,
    value: str,
    note: str | None = None,
) -> FocusSet:
    if not value.strip():
        raise TypedMemoryBadRequestError("focus value must be non-empty")
    new = value.strip()
    previous = await get_focus(user, db)

    block = await block_ops.upsert_block(
        user, db, FOCUS_KEY,
        value=new,
        scope="own",
        pinned=True,
        priority=100,
        description=_FOCUS_DEFAULT_DESCRIPTION,
        updated_by=_updated_by(user),
    )

    # Episode: log focus changes for history. Skip the episode when
    # the value is unchanged (no-op set shouldn't pollute the timeline).
    if previous.value == new:
        return FocusSet(value=new, updated_at=block.updated_at, episode_id="")

    parts = [f"Focus set to '{new}'."]
    if previous.value:
        parts.append(f"Previous focus: '{previous.value}'.")
    if note:
        parts.append(note)
    content = " ".join(parts)
    agent_name = getattr(user, "_default_agent_slug", None) or "typed-memory"
    result = await episode_ops.add_episode(
        user, agent_name,
        episode_ops.WriteRequest(
            namespace="",
            content=content,
            source_type="text",
            source_description="focus-change",
            reference_time=_now(),
            tags=["focus-change"],
        ),
        db,
    )
    return FocusSet(value=new, updated_at=block.updated_at, episode_id=result.episode_id)


# --- preferences -------------------------------------------------------------


@dataclass
class PreferenceItem:
    key: str
    value: Any
    description: str | None
    updated_at: datetime


def _pref_block_key(key: str) -> str:
    k = key.strip()
    if not k:
        raise TypedMemoryBadRequestError("preference key must be non-empty")
    if "/" in k or k.startswith("preferences."):
        # Avoid double-prefixing if the caller already included it.
        raise TypedMemoryBadRequestError(
            "preference key must be a bare identifier (we add the "
            "'preferences.' prefix). Got: " + k
        )
    return f"{PREF_PREFIX}{k}"


async def list_preferences(user: User, db: AsyncSession) -> list[PreferenceItem]:
    rows = await block_ops.list_blocks(
        user, db, scope="both", prefix=PREF_PREFIX,
    )
    out: list[PreferenceItem] = []
    for r in rows:
        bare = r.block_key[len(PREF_PREFIX):]
        out.append(PreferenceItem(
            key=bare, value=r.block_value,
            description=r.description, updated_at=r.updated_at,
        ))
    return out


async def set_preference(
    user: User,
    db: AsyncSession,
    *,
    key: str,
    value: Any,
    description: str | None = None,
) -> PreferenceItem:
    full = _pref_block_key(key)
    block = await block_ops.upsert_block(
        user, db, full,
        value=value,
        scope="global",   # preferences are user-level, not per-agent
        pinned=True,
        priority=90,
        description=description or _PREF_DEFAULT_DESCRIPTION_FMT.format(key=key),
        updated_by=_updated_by(user),
    )
    return PreferenceItem(
        key=key, value=block.block_value,
        description=block.description, updated_at=block.updated_at,
    )


# --- events (thin pass-through to episodes) ----------------------------------


@dataclass
class NotedEvent:
    episode_id: str
    namespace: str
    created_at: datetime


async def note_event(
    user: User,
    db: AsyncSession,
    *,
    content: str,
    namespace: str | None = None,
    reference_time: datetime | None = None,
    tags: list[str] | None = None,
    saga: str | None = None,
) -> NotedEvent:
    agent_name = getattr(user, "_default_agent_slug", None) or "typed-memory"
    result = await episode_ops.add_episode(
        user, agent_name,
        episode_ops.WriteRequest(
            namespace=namespace or "",
            content=content,
            source_type="text",
            source_description=f"note-event:{agent_name}",
            reference_time=reference_time or _now(),
            tags=tags or [],
            saga=saga,
        ),
        db,
    )
    return NotedEvent(
        episode_id=result.episode_id,
        namespace=result.namespace,
        created_at=result.created_at,
    )


# --- turn_context bundle -----------------------------------------------------


@dataclass
class PinnedBlock:
    key: str
    value: Any
    priority: int
    description: str | None
    scope: str


@dataclass
class RecalledEpisode:
    episode_id: str | None
    namespace: str
    fact: str
    score: float
    valid_at: datetime | None
    invalid_at: datetime | None


@dataclass
class TurnContext:
    active_tasks: list[TaskItem]
    current_focus: str | None
    preferences: dict[str, Any]
    pinned_blocks: list[PinnedBlock]
    recalled_episodes: list[RecalledEpisode]
    namespaces_searched: list[str]


async def build_turn_context(
    user: User,
    db: AsyncSession,
    *,
    query: str | None = None,
    recall_limit: int = 10,
    namespaces: list[str] | None = None,
) -> TurnContext:
    """One-shot context bundle for a turn.

    Always returns: all pinned blocks + structured shortcuts for the
    canonical keys (active_tasks / current_focus / preferences).

    Recall only fires when `query` is non-empty — a new conversation
    with no user message yet shouldn't burn a graphiti search.
    """
    # --- pinned (single SQL hit, both own + global)
    pinned_rows = await block_ops.list_blocks(user, db, scope="both", pinned=True)
    pinned = [
        PinnedBlock(
            key=r.block_key, value=r.block_value, priority=r.priority,
            description=r.description, scope=r.scope,
        )
        for r in pinned_rows
    ]

    # --- structured shortcuts (cheap, in-memory off the pinned list)
    tasks_value: list[dict[str, Any]] = []
    focus_value: str | None = None
    prefs: dict[str, Any] = {}
    for r in pinned:
        if r.key == TASKS_KEY and isinstance(r.value, list):
            tasks_value = [t for t in r.value if isinstance(t, dict)]
        elif r.key == FOCUS_KEY and isinstance(r.value, str):
            focus_value = r.value
        elif r.key.startswith(PREF_PREFIX):
            prefs[r.key[len(PREF_PREFIX):]] = r.value
    tasks = [TaskItem.from_dict(t) for t in tasks_value]
    tasks.sort(key=lambda t: (-t.priority, -t.updated_at.timestamp()))

    # --- recall (optional, query-gated)
    ns_searched: list[str] = []
    recalled: list[RecalledEpisode] = []
    if query and query.strip():
        # Default: caller's agent namespace + user:me. Match Smith's
        # auto-recall today so this endpoint is a drop-in replacement.
        if namespaces is None:
            default_ns = default_namespace_for(user).raw
            ns_searched = list(dict.fromkeys([default_ns, "user:me"]))
        else:
            ns_searched = list(namespaces)
        try:
            hits = await episode_ops.search(
                user, query.strip(), ns_searched, recall_limit, db,
            )
        except episode_ops.MemoryError as exc:
            # Don't let recall failure tank the whole turn — return
            # the pinned bundle, log, move on.
            _logger.warning("turn_context recall failed: %s", exc)
            hits = []
        for h in hits:
            recalled.append(RecalledEpisode(
                episode_id=h.source_episode_ids[0] if h.source_episode_ids else None,
                namespace=h.namespace, fact=h.fact, score=h.score or 0.0,
                valid_at=h.valid_at, invalid_at=h.invalid_at,
            ))

    return TurnContext(
        active_tasks=tasks,
        current_focus=focus_value,
        preferences=prefs,
        pinned_blocks=pinned,
        recalled_episodes=recalled,
        namespaces_searched=ns_searched,
    )
