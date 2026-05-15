"""Namespace consolidation — dedup + cleanup.

Three pieces:

1. **Detection**: read-only — scan a namespace, return a Plan describing
   what we'd do. Two strategies for MVP:
       - dedup-exact: fact-text-identical edges → keep oldest, invalidate
         the rest.
       - cleanup-tags: episodes with `forget` or `deprecated` tag in
         their metadata → delete the episode + any facts whose source
         episode is in that set.

2. **State**: per-namespace lock. While `apply()` is running on a
   namespace, the writer / reader entry points check
   `assert_namespace_unlocked(ns)` and raise NamespaceLockedError
   (mapped to HTTP 423 at the API edge). Lock is in-memory; ttl
   protects against crashes.

3. **Apply**: takes a plan_id (returned from detection), re-validates,
   acquires the lock, executes the actions, releases the lock.

Plans are persisted in-memory for 5 minutes — enough for an operator
to review and click apply, short enough that a stale plan doesn't
silently delete the wrong thing.

LLM-based semantic dedup is a follow-up; the hook (`mode="semantic"`)
is reserved in the public API so adding it later won't break callers.
"""
from __future__ import annotations

import contextvars
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memory_service.core.namespaces import Namespace, parse
from memory_service.models import EpisodeMetadata

_logger = logging.getLogger(__name__)


# ---------- per-namespace lock ----------


# {namespace_raw: (started_at, mode, deadline_monotonic)}
_locks: dict[str, tuple[datetime, str, float]] = {}

# 5 minutes max — apply that takes longer is almost certainly hung.
_LOCK_TTL_SECONDS = 5 * 60

# Context flag flipped on while apply() is running so the lock check
# doesn't deadlock when apply calls the very entry points it locked.
_inside_apply: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_inside_apply", default=False,
)


def assert_namespace_unlocked(ns: Namespace | str) -> None:
    """Call at the top of every read/write entry point that touches a
    consolidatable namespace. Imports the error type lazily to avoid
    cycling memory.py → consolidation.py → memory.py.
    """
    if _inside_apply.get():
        return  # apply path is allowed to mutate while holding the lock
    raw = ns.raw if isinstance(ns, Namespace) else ns
    entry = _locks.get(raw)
    if entry is None:
        return
    started_at, mode, deadline = entry
    if time.monotonic() > deadline:
        _locks.pop(raw, None)
        _logger.warning("namespace %s lock expired (mode=%s) — released", raw, mode)
        return
    from memory_service.core.memory import NamespaceSleepingError

    raise NamespaceSleepingError(
        f"Namespace {raw!r} is sleeping — consolidation in progress "
        f"since {started_at.isoformat()} (mode={mode})"
    )


def _acquire_lock(ns_raw: str, mode: str) -> None:
    if ns_raw in _locks:
        started_at, existing_mode, _ = _locks[ns_raw]
        from memory_service.core.memory import NamespaceSleepingError

        raise NamespaceSleepingError(
            f"Namespace {ns_raw!r} is already sleeping (apply since "
            f"{started_at.isoformat()}, mode={existing_mode})"
        )
    _locks[ns_raw] = (
        datetime.now(UTC),
        mode,
        time.monotonic() + _LOCK_TTL_SECONDS,
    )


def _release_lock(ns_raw: str) -> None:
    _locks.pop(ns_raw, None)


def lock_status(ns_raw: str) -> dict[str, Any] | None:
    """For /v1/consolidate/status."""
    entry = _locks.get(ns_raw)
    if entry is None:
        return None
    started_at, mode, deadline = entry
    if time.monotonic() > deadline:
        _locks.pop(ns_raw, None)
        return None
    return {
        "status": "sleeping",
        "mode": mode,
        "started_at": started_at.isoformat(),
        "ttl_seconds_remaining": int(deadline - time.monotonic()),
    }


# ---------- plan model ----------


class ActionType(str, Enum):
    INVALIDATE_FACT = "invalidate_fact"   # soft: set invalid_at=now
    DELETE_FACT = "delete_fact"           # hard: edge.delete()
    DELETE_EPISODE = "delete_episode"     # hard: episode + cascading entities


@dataclass
class PlannedAction:
    type: ActionType
    target_id: str       # edge_uuid or episode_id
    reason: str
    # For dedup: facts we'd KEEP, for cross-reference in the UI.
    kept_id: str | None = None
    # Human-readable label for the UI.
    label: str = ""


@dataclass
class ConsolidationPlan:
    plan_id: str
    namespace: str
    mode: str
    created_at: datetime
    actions: list[PlannedAction] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def expired(self) -> bool:
        return datetime.now(UTC) - self.created_at > timedelta(minutes=5)


# In-memory plan store. Plans expire after 5 min — long enough for
# review, short enough that an old browser tab doesn't apply yesterday's
# state to today's namespace.
_plans: dict[str, ConsolidationPlan] = {}


def store_plan(plan: ConsolidationPlan) -> None:
    # Opportunistic GC of expired plans.
    for pid in [k for k, v in _plans.items() if v.expired]:
        _plans.pop(pid, None)
    _plans[plan.plan_id] = plan


def get_plan(plan_id: str) -> ConsolidationPlan | None:
    p = _plans.get(plan_id)
    if p is None:
        return None
    if p.expired:
        _plans.pop(plan_id, None)
        return None
    return p


def discard_plan(plan_id: str) -> None:
    _plans.pop(plan_id, None)


# ---------- detection ----------


async def _all_facts_in_namespace(
    ns: Namespace,
    db: AsyncSession,  # noqa: ARG001 — kept for symmetry / future use
) -> list[dict[str, Any]]:
    """Pull every EntityEdge (RELATES_TO) in the namespace from FalkorDB.

    We can't push exact-text grouping into FalkorDB cleanly from here
    without a custom Cypher; the per-namespace edge counts are small
    enough (hundreds to low thousands) that pulling client-side and
    bucketing in Python is fine for MVP.
    """
    # Avoid a circular import; memory.py owns the Graphiti client.
    from memory_service.core.memory import _driver_for_namespace, _require_client

    client = _require_client()
    driver = _driver_for_namespace(client, ns)

    # FalkorDB / Graphiti edge label is RELATES_TO. valid_at + invalid_at
    # are timestamps stored as properties; we want non-invalidated only.
    query = """
    MATCH (a)-[r:RELATES_TO]->(b)
    WHERE r.invalid_at IS NULL
    RETURN r.uuid AS uuid, r.fact AS fact, r.valid_at AS valid_at,
           r.created_at AS created_at, r.name AS name,
           a.uuid AS source_uuid, b.uuid AS target_uuid
    """
    rows, _, _ = await driver.execute_query(query)
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not row.get("uuid") or not row.get("fact"):
            continue
        out.append(row)
    return out


def _dedup_exact_plan(facts: list[dict[str, Any]]) -> list[PlannedAction]:
    """Group facts by normalised text. For each cluster with >1 facts,
    invalidate all but the oldest valid_at (keeping the original)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for f in facts:
        key = (f["fact"] or "").strip().lower()
        if not key:
            continue
        groups.setdefault(key, []).append(f)

    actions: list[PlannedAction] = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        # Keep the one with the earliest valid_at (the "original" assertion).
        members.sort(key=lambda x: x.get("valid_at") or x.get("created_at") or "9999")
        keep = members[0]
        for dupe in members[1:]:
            actions.append(
                PlannedAction(
                    type=ActionType.INVALIDATE_FACT,
                    target_id=dupe["uuid"],
                    reason=f"exact-text duplicate of {keep['uuid']}",
                    kept_id=keep["uuid"],
                    label=(dupe.get("fact") or "")[:120],
                )
            )
    return actions


# Hard cap so an unbounded namespace doesn't fire a 200k-token LLM call.
# When the namespace is bigger than this, semantic dedup returns an empty
# action list with a warning; the operator should chunk by hand for now.
_SEMANTIC_FACT_CAP = 200


async def _dedup_semantic_plan(
    facts: list[dict[str, Any]],
) -> list[PlannedAction]:
    """Ask the Graphiti-bound LLM to cluster semantically-equivalent facts.

    Single batched call. For each cluster ≥2 the LLM picks the "best"
    fact to keep (most specific / earliest stated) and we invalidate
    the rest. If anything goes wrong (LLM down, bad JSON, payload too
    big), we return an empty plan — never half-process; the operator
    can fall back to dedup-exact.
    """
    if not facts:
        return []
    if len(facts) > _SEMANTIC_FACT_CAP:
        _logger.warning(
            "semantic dedup: %d facts exceeds cap %d — skipping. "
            "Run dedup-exact first or wait for chunked-semantic support.",
            len(facts), _SEMANTIC_FACT_CAP,
        )
        return []

    from pydantic import BaseModel, Field as PField

    from memory_service.core.memory import _require_client

    class _Cluster(BaseModel):
        keep_uuid: str = PField(description="UUID of the fact to KEEP from this cluster")
        duplicate_uuids: list[str] = PField(
            description="UUIDs of facts in this cluster that should be invalidated as duplicates of keep_uuid",
        )
        rationale: str = PField(description="One-line reason these are duplicates")

    class _DedupResponse(BaseModel):
        clusters: list[_Cluster] = PField(
            description=(
                "List of duplicate clusters. Each cluster must have at least 2 facts "
                "(one keep + one or more duplicates). Skip facts that have no "
                "semantic duplicate."
            ),
        )

    # Compact input for the LLM — only id + fact text.
    fact_lines = "\n".join(
        f"  {f['uuid']}  {(f.get('fact') or '').strip()}"
        for f in facts
    )
    prompt_text = (
        "Identify clusters of SEMANTICALLY EQUIVALENT facts below. Two facts "
        "are equivalent if they assert the same thing about the same subject, "
        "even with different wording, tense, or detail level.\n\n"
        "Rules:\n"
        "  - Be conservative. Only cluster facts you're confident are saying "
        "the same thing. When in doubt, do NOT cluster.\n"
        "  - For each cluster of N facts, pick exactly ONE keep_uuid and put "
        "the other N-1 in duplicate_uuids.\n"
        "  - Prefer keeping the most specific / earliest / clearest variant.\n"
        "  - It is fine to return an empty cluster list if no equivalents exist.\n\n"
        f"Facts (uuid  fact):\n{fact_lines}"
    )

    try:
        client = _require_client()
        from graphiti_core.prompts.models import Message

        llm = client.llm_client
        response = await llm.generate_response(
            [Message(role="user", content=prompt_text)],
            response_model=_DedupResponse,
        )
        # graphiti_core returns a dict; coerce
        parsed = _DedupResponse.model_validate(response)
    except Exception as exc:  # noqa: BLE001
        _logger.exception("semantic dedup LLM call failed: %s", exc)
        return []

    by_uuid = {f["uuid"]: f for f in facts}
    actions: list[PlannedAction] = []
    for cluster in parsed.clusters:
        keep = by_uuid.get(cluster.keep_uuid)
        if keep is None:
            continue
        for dup_uuid in cluster.duplicate_uuids:
            if dup_uuid == cluster.keep_uuid:
                continue
            dup = by_uuid.get(dup_uuid)
            if dup is None:
                continue
            actions.append(
                PlannedAction(
                    type=ActionType.INVALIDATE_FACT,
                    target_id=dup_uuid,
                    reason=f"semantic duplicate of {cluster.keep_uuid} — {cluster.rationale}",
                    kept_id=cluster.keep_uuid,
                    label=(dup.get("fact") or "")[:120],
                )
            )
    return actions


async def _cleanup_tags_plan(
    ns: Namespace, db: AsyncSession,
) -> list[PlannedAction]:
    """Find episodes with `forget` or `deprecated` tags in this namespace
    and queue them for deletion. NOTE: we delete the EPISODE; the
    cascading entity/fact cleanup is Graphiti's job (`node.delete()`
    detaches related entities).
    """
    FORGET_TAGS = {"forget", "deprecated", "forget-me"}
    stmt = select(EpisodeMetadata).where(EpisodeMetadata.namespace == ns.raw)
    rows = (await db.execute(stmt)).scalars().all()
    actions: list[PlannedAction] = []
    for ep in rows:
        if not ep.tags:
            continue
        matched = FORGET_TAGS & {t.lower() for t in ep.tags}
        if not matched:
            continue
        actions.append(
            PlannedAction(
                type=ActionType.DELETE_EPISODE,
                target_id=ep.id,
                reason=f"tagged {sorted(matched)} for cleanup",
                label=(ep.created_by_agent or "?") + " · " + ", ".join(sorted(matched)),
            )
        )
    return actions


async def build_plan(
    ns: Namespace,
    mode: Literal["dedup-exact", "dedup-semantic", "cleanup-tags", "all"],
    db: AsyncSession,
) -> ConsolidationPlan:
    """Pure read; safe to call without the namespace lock.

    Mode `all` runs dedup-exact (cheap) AND cleanup-tags but NOT
    semantic (expensive — opt-in only). To run everything, call twice:
    first mode='all', apply, then mode='dedup-semantic' to catch the
    semantic dupes the exact pass missed.
    """
    actions: list[PlannedAction] = []
    facts: list[dict[str, Any]] | None = None
    if mode in ("dedup-exact", "all"):
        facts = await _all_facts_in_namespace(ns, db)
        actions.extend(_dedup_exact_plan(facts))
    if mode == "dedup-semantic":
        # Don't re-fetch if dedup-exact already pulled the facts.
        if facts is None:
            facts = await _all_facts_in_namespace(ns, db)
        actions.extend(await _dedup_semantic_plan(facts))
    if mode in ("cleanup-tags", "all"):
        actions.extend(await _cleanup_tags_plan(ns, db))

    counts = {
        "invalidate_fact": sum(1 for a in actions if a.type == ActionType.INVALIDATE_FACT),
        "delete_fact": sum(1 for a in actions if a.type == ActionType.DELETE_FACT),
        "delete_episode": sum(1 for a in actions if a.type == ActionType.DELETE_EPISODE),
        "total": len(actions),
    }
    plan = ConsolidationPlan(
        plan_id=str(uuid.uuid4()),
        namespace=ns.raw,
        mode=mode,
        created_at=datetime.now(UTC),
        actions=actions,
        counts=counts,
    )
    store_plan(plan)
    return plan


# ---------- apply ----------


@dataclass
class ApplyResult:
    plan_id: str
    namespace: str
    applied: int
    failed: int
    errors: list[dict[str, str]]
    started_at: datetime
    completed_at: datetime


async def apply_plan(
    plan: ConsolidationPlan, user, db: AsyncSession,  # noqa: ANN001 — user import would cycle
) -> ApplyResult:
    """Acquires the lock for `plan.namespace`, runs every action, releases.

    We use the existing single-action primitives in core.memory
    (set_fact_invalid_at / delete_fact / delete_episode) so audit + permission
    logic stays in one place. Failure of an individual action is logged
    + counted but doesn't abort the rest of the plan.
    """
    from memory_service.core.memory import (
        delete_episode,
        delete_fact,
        set_fact_invalid_at,
    )

    _acquire_lock(plan.namespace, mode=f"apply:{plan.mode}")
    started_at = datetime.now(UTC)
    applied = 0
    errors: list[dict[str, str]] = []
    # Flip the bypass flag so our own delete/invalidate calls don't
    # trip the lock check. Reset in finally so a leaked flag can't
    # silently disable consolidation on the next request.
    token = _inside_apply.set(True)
    try:
        for action in plan.actions:
            try:
                if action.type == ActionType.INVALIDATE_FACT:
                    await set_fact_invalid_at(user, action.target_id, started_at, db)
                elif action.type == ActionType.DELETE_FACT:
                    await delete_fact(user, action.target_id, db)
                elif action.type == ActionType.DELETE_EPISODE:
                    await delete_episode(user, action.target_id, db)
                applied += 1
            except Exception as exc:  # noqa: BLE001
                _logger.exception(
                    "consolidate apply action failed: %s %s",
                    action.type.value, action.target_id,
                )
                errors.append({
                    "action_type": action.type.value,
                    "target_id": action.target_id,
                    "error": str(exc),
                })
    finally:
        _inside_apply.reset(token)
        _release_lock(plan.namespace)
        # Plans are one-shot; remove from store so a refresh doesn't
        # re-apply against the post-apply state.
        discard_plan(plan.plan_id)

    return ApplyResult(
        plan_id=plan.plan_id,
        namespace=plan.namespace,
        applied=applied,
        failed=len(errors),
        errors=errors,
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )
