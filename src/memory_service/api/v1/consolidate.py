"""/v1/consolidate — namespace dedup + cleanup.

Two-step contract:

  POST /v1/consolidate/plan       review-only, returns a Plan
  POST /v1/consolidate/apply      commits a Plan by id
  GET  /v1/consolidate/status     show lock state for a namespace

The split forces the operator (or smith) to look at what's about to
happen before any data changes. Plans are TTL-cached in memory for
5 minutes.

While `apply` is running, the namespace's writes / reads return 423
Locked via the lock checked in core/memory.py write paths.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from memory_service.api.deps import CurrentUser, DBDep
from memory_service.core.consolidation import (
    apply_plan,
    build_plan,
    get_plan,
    lock_status,
)
from memory_service.core.namespaces import NamespaceError, can_write, resolve

router = APIRouter(prefix="/consolidate", tags=["consolidate"])

ConsolidateMode = Literal["dedup-exact", "cleanup-tags", "all"]


# ---------- request / response shapes ----------


class PlanRequest(BaseModel):
    namespace: str = Field(
        ...,
        description=(
            "Namespace to consolidate. Accepts shortcuts like 'user:me' / "
            "'agent:me/<slug>'. Caller must have WRITE permission."
        ),
    )
    mode: ConsolidateMode = Field(
        default="all",
        description=(
            "dedup-exact: merge facts with identical text (cheap). "
            "cleanup-tags: delete episodes tagged 'forget' / 'deprecated'. "
            "all: both."
        ),
    )


class PlannedActionOut(BaseModel):
    type: str
    target_id: str
    reason: str
    kept_id: str | None = None
    label: str


class PlanResponse(BaseModel):
    plan_id: str
    namespace: str
    mode: str
    created_at: str
    expires_at: str
    counts: dict[str, int]
    actions: list[PlannedActionOut]


class ApplyRequest(BaseModel):
    plan_id: str = Field(min_length=1)


class ApplyResponse(BaseModel):
    plan_id: str
    namespace: str
    applied: int
    failed: int
    errors: list[dict[str, str]]
    started_at: str
    completed_at: str


class StatusResponse(BaseModel):
    namespace: str
    state: dict[str, object] | None = Field(
        default=None,
        description=(
            "null when idle. When sleeping: "
            "{status:'sleeping', mode, started_at, ttl_seconds_remaining}"
        ),
    )


# ---------- endpoints ----------


@router.post("/plan", response_model=PlanResponse)
async def make_plan(payload: PlanRequest, user: CurrentUser, db: DBDep) -> PlanResponse:
    try:
        ns = resolve(payload.namespace, user)
    except NamespaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not await can_write(user, ns, db):
        raise HTTPException(
            status_code=403,
            detail=f"You don't have write access to {ns.raw!r}",
        )
    plan = await build_plan(ns, payload.mode, db)
    from datetime import timedelta

    return PlanResponse(
        plan_id=plan.plan_id,
        namespace=plan.namespace,
        mode=plan.mode,
        created_at=plan.created_at.isoformat(),
        expires_at=(plan.created_at + timedelta(minutes=5)).isoformat(),
        counts=plan.counts,
        actions=[
            PlannedActionOut(
                type=a.type.value,
                target_id=a.target_id,
                reason=a.reason,
                kept_id=a.kept_id,
                label=a.label,
            )
            for a in plan.actions
        ],
    )


@router.post("/apply", response_model=ApplyResponse)
async def commit_plan(payload: ApplyRequest, user: CurrentUser, db: DBDep) -> ApplyResponse:
    plan = get_plan(payload.plan_id)
    if plan is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Plan {payload.plan_id!r} not found or expired. "
                "Re-run POST /v1/consolidate/plan."
            ),
        )
    try:
        ns = resolve(plan.namespace, user)
    except NamespaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not await can_write(user, ns, db):
        raise HTTPException(
            status_code=403,
            detail=f"You don't have write access to {ns.raw!r}",
        )
    from memory_service.core.memory import NamespaceSleepingError

    try:
        result = await apply_plan(plan, user, db)
    except NamespaceSleepingError as exc:
        # Someone else's apply is already running on this namespace.
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED, detail=str(exc),
        ) from exc
    return ApplyResponse(
        plan_id=result.plan_id,
        namespace=result.namespace,
        applied=result.applied,
        failed=result.failed,
        errors=result.errors,
        started_at=result.started_at.isoformat(),
        completed_at=result.completed_at.isoformat(),
    )


@router.get("/status", response_model=StatusResponse)
async def status_for_namespace(namespace: str, user: CurrentUser, db: DBDep) -> StatusResponse:
    try:
        ns = resolve(namespace, user)
    except NamespaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # No permission check here — knowing whether a namespace is sleeping
    # leaks at most "someone is consolidating it", which is fine for any
    # authed caller.
    _ = await can_write(user, ns, db)  # warm the can_write path so misuses surface
    return StatusResponse(namespace=ns.raw, state=lock_status(ns.raw))
