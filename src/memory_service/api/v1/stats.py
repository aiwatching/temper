"""/v1/stats — operational summary for the admin dashboard.

Cheap aggregate counts so a single page load lights up "what's in this
service right now." Authenticated; super_admin sees system-wide totals,
regular users see scoped to their readable namespaces.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from memory_service.api.deps import CurrentUser, DBDep
from memory_service.core.namespaces import readable_namespaces_for
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

    out = StatsResponse(
        episodes_total=sum(ep_counts.values()),
        episodes_pending=ep_counts.get("pending", 0),
        episodes_failed=ep_counts.get("failed", 0),
        schemas_count=(await db.execute(schema_stmt)).scalar_one(),
        users_count=None,
        orgs_count=None,
        groups_count=None,
        readable_namespaces=readable_raw,
    )

    if user.is_super_admin:
        out.users_count = (await db.execute(select(func.count(User.id)))).scalar_one()
        out.orgs_count = (
            await db.execute(select(func.count(Organization.id)))
        ).scalar_one()
        out.groups_count = (await db.execute(select(func.count(Group.id)))).scalar_one()

    return out
