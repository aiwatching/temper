"""/v1/stats — operational summary for the admin dashboard.

Cheap aggregate counts so a single page load lights up "what's in this
service right now." Authenticated; super_admin sees system-wide totals,
regular users see scoped to their readable namespaces.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from memory_service.api.deps import CurrentUser, DBDep
from memory_service.core.namespaces import NamespaceError, readable_namespaces_for, resolve
from memory_service.models import EntitySchema, EpisodeMetadata, Group, Organization, User

router = APIRouter(tags=["stats"])


class StatsResponse(BaseModel):
    episodes_total: int
    episodes_pending: int
    episodes_failed: int
    schemas_count: int
    users_count: int | None  # super_admin only
    orgs_count: int | None
    groups_count: int | None
    super_admin_count: int  # whole system; lets the UI flag "no admin yet"
    readable_namespaces: list[str]


@router.get("/stats", response_model=StatsResponse)
async def stats(user: CurrentUser, db: DBDep) -> StatsResponse:
    readable = await readable_namespaces_for(user, db)
    readable_raw = [n.raw for n in readable]

    ep_stmt = select(EpisodeMetadata.extraction_status, func.count())
    if not user.is_super_admin:
        ep_stmt = ep_stmt.where(EpisodeMetadata.namespace.in_(readable_raw))
    ep_stmt = ep_stmt.group_by(EpisodeMetadata.extraction_status)
    ep_counts: dict[str, int] = dict((await db.execute(ep_stmt)).all())

    schema_stmt = select(func.count(EntitySchema.id))
    if not user.is_super_admin:
        schema_stmt = schema_stmt.where(EntitySchema.namespace.in_(readable_raw))

    super_admin_count = (
        await db.execute(
            select(func.count(User.id)).where(User.is_super_admin.is_(True))
        )
    ).scalar_one()

    out = StatsResponse(
        episodes_total=sum(ep_counts.values()),
        episodes_pending=ep_counts.get("pending", 0),
        episodes_failed=ep_counts.get("failed", 0),
        schemas_count=(await db.execute(schema_stmt)).scalar_one(),
        users_count=None,
        orgs_count=None,
        groups_count=None,
        super_admin_count=super_admin_count,
        readable_namespaces=readable_raw,
    )

    if user.is_super_admin:
        out.users_count = (await db.execute(select(func.count(User.id)))).scalar_one()
        out.orgs_count = (
            await db.execute(select(func.count(Organization.id)))
        ).scalar_one()
        out.groups_count = (await db.execute(select(func.count(Group.id)))).scalar_one()

    return out


# ---------- daily episode counts ----------


class DailyEpisodePoint(BaseModel):
    date: str   # YYYY-MM-DD, UTC
    count: int


class DailyEpisodesResponse(BaseModel):
    from_date: str
    to_date: str
    namespace: str | None
    total: int
    points: list[DailyEpisodePoint]


# Bound the window so we don't accidentally let the client ask for "5 years
# bucketed by day" and pin the DB. 365 days is plenty for the dashboard.
_MAX_DAYS = 365


@router.get("/stats/episodes/daily", response_model=DailyEpisodesResponse)
async def daily_episodes(
    user: CurrentUser,
    db: DBDep,
    days: Annotated[
        int,
        Query(
            ge=1, le=_MAX_DAYS,
            description="Window length ending today (UTC). Capped at 365.",
        ),
    ] = 30,
    namespace: Annotated[
        str | None,
        Query(
            description=(
                "Restrict to one namespace. Accepts shortcuts like "
                "'user:me' / 'agent:me/<slug>'. Omit to count every "
                "namespace the caller can read."
            ),
        ),
    ] = None,
) -> DailyEpisodesResponse:
    """Day-by-day episode-creation counts, UTC.

    Returns one point per day in `[today - days + 1, today]`, including
    zeros so the client can draw a continuous bar chart without
    interpolating. Scoped to the caller's readable namespaces unless
    they're super_admin (who sees system-wide totals).
    """
    today = datetime.now(UTC).date()
    from_date = today - timedelta(days=days - 1)

    stmt = select(
        func.date(EpisodeMetadata.created_at).label("d"),
        func.count().label("n"),
    ).where(EpisodeMetadata.created_at >= datetime.combine(from_date, datetime.min.time(), tzinfo=UTC))

    if namespace:
        try:
            ns = resolve(namespace, user)
        except NamespaceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # Verify the caller can read it (mirrors search.py's tightening).
        readable = await readable_namespaces_for(user, db)
        if ns.raw not in {n.raw for n in readable} and not user.is_super_admin:
            raise HTTPException(
                status_code=403, detail=f"You can't read {ns.raw!r}",
            )
        stmt = stmt.where(EpisodeMetadata.namespace == ns.raw)
        echo_ns = ns.raw
    elif not user.is_super_admin:
        readable = await readable_namespaces_for(user, db)
        stmt = stmt.where(EpisodeMetadata.namespace.in_([n.raw for n in readable]))
        echo_ns = None
    else:
        echo_ns = None

    stmt = stmt.group_by("d").order_by("d")
    rows = (await db.execute(stmt)).all()
    # Bucket by ISO date string. DB drivers return mixed shapes (date,
    # datetime, or string depending on dialect), so coerce.
    by_day: dict[str, int] = {}
    for d, n in rows:
        if isinstance(d, datetime):
            d = d.date()
        if isinstance(d, date):
            key = d.isoformat()
        else:
            key = str(d)[:10]
        by_day[key] = int(n)

    # Fill zero-count days so the client just plots a fixed-length array.
    points: list[DailyEpisodePoint] = []
    cursor = from_date
    while cursor <= today:
        iso = cursor.isoformat()
        points.append(DailyEpisodePoint(date=iso, count=by_day.get(iso, 0)))
        cursor += timedelta(days=1)

    return DailyEpisodesResponse(
        from_date=from_date.isoformat(),
        to_date=today.isoformat(),
        namespace=echo_ns,
        total=sum(p.count for p in points),
        points=points,
    )
