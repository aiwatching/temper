"""/v1/groups — flat groups within an organization.

All mutating ops (create, rename, delete, add/remove member) require
super_admin. Org members can list + read groups they belong to so they
know which `group:<slug>` namespaces are available for memory sharing.

A user added to a group MUST belong to the same org as the group.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from memory_service.api.deps import CurrentUser, DBDep
from memory_service.models import (
    Group,
    Organization,
    User,
    UserGroupMembership,
)
from memory_service.schemas.group import (
    GroupCreate,
    GroupMemberAdd,
    GroupMemberOut,
    GroupMemberRoleUpdate,
    GroupOut,
    GroupUpdate,
)

router = APIRouter(prefix="/groups", tags=["groups"])


# ---------- helpers ----------


async def _group_by_slug(
    db: AsyncSession, slug: str
) -> tuple[Group, Organization | None]:
    """Find a group by slug. Returns (group, org) where org may be None
    if the group has been orphaned — its parent org was deleted before
    cascade-delete was wired up. Orphan groups stay readable + deletable
    so operators can clean them up; everything else (write, member add,
    rename) requires a live org.
    """
    row = (
        await db.execute(
            select(Group, Organization)
            .outerjoin(Organization, Organization.id == Group.org_id)
            .where(Group.slug == slug)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Group '{slug}' not found")
    return row[0], row[1]


def _require_live_org(group: Group, org: Organization | None, action: str) -> Organization:
    """Reject mutating ops on orphan groups with a clear hint."""
    if org is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Group '{group.slug}' is orphaned — its parent org was deleted. "
                f"Cannot {action}. Delete the group via DELETE /v1/groups/{group.slug} "
                "to clean it up."
            ),
        )
    return org


async def _membership(
    db: AsyncSession, user_id: str, group_id: str
) -> UserGroupMembership | None:
    return (
        await db.execute(
            select(UserGroupMembership).where(
                UserGroupMembership.user_id == user_id,
                UserGroupMembership.group_id == group_id,
            )
        )
    ).scalar_one_or_none()


async def _member_count(db: AsyncSession, group_id: str) -> int:
    return int(
        (
            await db.execute(
                select(func.count(UserGroupMembership.id)).where(
                    UserGroupMembership.group_id == group_id
                )
            )
        ).scalar_one()
    )


async def _require_super_admin(user: User) -> None:
    if user.is_super_admin:
        return
    raise HTTPException(
        status_code=403, detail="Only super_admin may perform this action",
    )


async def _require_group_member(
    db: AsyncSession, user: User, group: Group, org: Organization
) -> None:
    if user.is_super_admin:
        return
    if await _membership(db, user.id, group.id) is not None:
        return
    raise HTTPException(
        status_code=403, detail=f"Only members of group '{group.slug}' may read it"
    )


def _serialize_group(
    group: Group, org_slug: str | None, member_count: int
) -> GroupOut:
    return GroupOut(
        id=group.id,
        slug=group.slug,
        name=group.name,
        org_slug=org_slug,
        status="orphan" if org_slug is None else "ok",
        created_at=group.created_at,
        member_count=member_count,
    )


async def _org_by_slug(db: AsyncSession, slug: str) -> Organization:
    org = (
        await db.execute(select(Organization).where(Organization.slug == slug))
    ).scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=404, detail=f"Org '{slug}' not found")
    return org


# ---------- group CRUD ----------


@router.post("", status_code=status.HTTP_201_CREATED, response_model=GroupOut)
async def create_group(payload: GroupCreate, user: CurrentUser, db: DBDep) -> GroupOut:
    await _require_super_admin(user)
    if not payload.org_slug:
        raise HTTPException(
            status_code=400,
            detail="org_slug is required — super_admin must say which org owns the group",
        )
    org = await _org_by_slug(db, payload.org_slug)

    existing = (
        await db.execute(select(Group).where(Group.slug == payload.slug))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409, detail=f"Group slug '{payload.slug}' already taken"
        )

    group = Group(slug=payload.slug, name=payload.name, org_id=org.id)
    db.add(group)
    await db.flush()
    await db.commit()
    await db.refresh(group)
    return _serialize_group(group, org.slug, await _member_count(db, group.id))


@router.get("", response_model=list[GroupOut])
async def list_groups(user: CurrentUser, db: DBDep) -> list[GroupOut]:
    """List groups the caller can see.

    super_admin sees everything (including orphan groups whose parent
    org was deleted — those have status="orphan" so the operator can
    spot + clean them up). Org members see live groups in their own
    org. Users without an org see nothing.
    """
    if user.is_super_admin:
        # LEFT OUTER JOIN so orphan groups still show up.
        stmt = (
            select(Group, Organization)
            .outerjoin(Organization, Organization.id == Group.org_id)
            .order_by(Group.created_at.desc())
        )
    elif user.org_id:
        # Regular org members only see live groups in their org. Orphans
        # by definition have no live org, so they can never belong to
        # the user's org — filtering by org_id alone is correct.
        stmt = (
            select(Group, Organization)
            .join(Organization, Organization.id == Group.org_id)
            .where(Group.org_id == user.org_id)
            .order_by(Group.created_at.desc())
        )
    else:
        return []
    rows = list((await db.execute(stmt)).all())
    return [
        _serialize_group(g, o.slug if o else None, await _member_count(db, g.id))
        for (g, o) in rows
    ]


@router.get("/{slug}", response_model=GroupOut)
async def get_group(slug: str, user: CurrentUser, db: DBDep) -> GroupOut:
    group, org = await _group_by_slug(db, slug)
    if org is None:
        # Orphan group: only super_admin sees it (so they can clean up).
        if not user.is_super_admin:
            raise HTTPException(status_code=404, detail=f"Group '{slug}' not found")
    else:
        await _require_group_member(db, user, group, org)
    return _serialize_group(
        group, org.slug if org else None, await _member_count(db, group.id)
    )


@router.patch("/{slug}", response_model=GroupOut)
async def update_group(
    slug: str, payload: GroupUpdate, user: CurrentUser, db: DBDep
) -> GroupOut:
    group, org = await _group_by_slug(db, slug)
    org = _require_live_org(group, org, "rename")
    await _require_super_admin(user)
    group.name = payload.name
    await db.commit()
    await db.refresh(group)
    return _serialize_group(group, org.slug, await _member_count(db, group.id))


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(slug: str, user: CurrentUser, db: DBDep) -> None:
    group, org = await _group_by_slug(db, slug)
    await _require_super_admin(user)
    await db.delete(group)  # FK cascade drops memberships
    await db.commit()
    # FalkorDB doesn't know about our FKs — explicit graph drop.
    from memory_service.core.memory import drop_namespace_graph

    await drop_namespace_graph(f"group:{slug}")
    return None


# ---------- members ----------


@router.post(
    "/{slug}/members",
    status_code=status.HTTP_201_CREATED,
    response_model=GroupMemberOut,
)
async def add_member(
    slug: str, payload: GroupMemberAdd, user: CurrentUser, db: DBDep
) -> GroupMemberOut:
    group, org = await _group_by_slug(db, slug)
    org = _require_live_org(group, org, "add members")
    await _require_super_admin(user)
    target = await db.get(User, payload.user_id)
    if target is None:
        raise HTTPException(status_code=404, detail=f"User '{payload.user_id}' not found")
    if target.org_id != org.id:
        raise HTTPException(
            status_code=409,
            detail=f"User must be a member of org '{org.slug}' before joining its groups",
        )
    if await _membership(db, target.id, group.id) is not None:
        raise HTTPException(
            status_code=409, detail=f"User is already a member of group '{slug}'"
        )
    db.add(UserGroupMembership(user_id=target.id, group_id=group.id, role=payload.role))
    await db.commit()
    return GroupMemberOut(
        user_id=target.id,
        email=target.email,
        display_name=target.display_name,
        role=payload.role,
    )


@router.get("/{slug}/members", response_model=list[GroupMemberOut])
async def list_members(
    slug: str, user: CurrentUser, db: DBDep
) -> list[GroupMemberOut]:
    group, org = await _group_by_slug(db, slug)
    if org is None:
        if not user.is_super_admin:
            raise HTTPException(status_code=404, detail=f"Group '{slug}' not found")
    else:
        await _require_group_member(db, user, group, org)
    rows = list(
        (
            await db.execute(
                select(User, UserGroupMembership.role)
                .join(UserGroupMembership, UserGroupMembership.user_id == User.id)
                .where(UserGroupMembership.group_id == group.id)
                .order_by(UserGroupMembership.created_at.desc())
            )
        ).all()
    )
    return [
        GroupMemberOut(
            user_id=u.id,
            email=u.email,
            display_name=u.display_name,
            role=role,
        )
        for (u, role) in rows
    ]


@router.patch("/{slug}/members/{user_id}", response_model=GroupMemberOut)
async def update_member_role(
    slug: str,
    user_id: str,
    payload: GroupMemberRoleUpdate,
    user: CurrentUser,
    db: DBDep,
) -> GroupMemberOut:
    group, org = await _group_by_slug(db, slug)
    org = _require_live_org(group, org, "change member roles")
    await _require_super_admin(user)
    m = await _membership(db, user_id, group.id)
    if m is None:
        raise HTTPException(
            status_code=404, detail=f"User '{user_id}' is not a member of group '{slug}'"
        )
    target = await db.get(User, user_id)
    assert target is not None
    m.role = payload.role
    await db.commit()
    return GroupMemberOut(
        user_id=target.id,
        email=target.email,
        display_name=target.display_name,
        role=payload.role,
    )


@router.delete("/{slug}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    slug: str, user_id: str, user: CurrentUser, db: DBDep
) -> None:
    group, org = await _group_by_slug(db, slug)
    # Self-leave is always allowed; otherwise only super_admin.
    if user_id != user.id:
        await _require_super_admin(user)
    m = await _membership(db, user_id, group.id)
    if m is None:
        raise HTTPException(
            status_code=404, detail=f"User '{user_id}' is not a member of group '{slug}'"
        )
    await db.delete(m)
    await db.commit()
    return None
