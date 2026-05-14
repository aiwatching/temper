"""/v1/groups — flat groups within an organization.

Group creation is open to any org member: the creator becomes the
group's first admin (UserGroupMembership.role='admin'). Org admins and
super_admin can manage any group in their scope; the group's own admins
manage that group's members.

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


async def _group_by_slug(db: AsyncSession, slug: str) -> tuple[Group, Organization]:
    row = (
        await db.execute(
            select(Group, Organization)
            .join(Organization, Organization.id == Group.org_id)
            .where(Group.slug == slug)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Group '{slug}' not found")
    return row[0], row[1]


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


def _is_org_admin_of(user: User, org: Organization) -> bool:
    return user.org_id == org.id and user.is_org_admin


async def _is_group_admin(db: AsyncSession, user: User, group: Group) -> bool:
    m = await _membership(db, user.id, group.id)
    return m is not None and m.role == "admin"


async def _require_group_admin(
    db: AsyncSession, user: User, group: Group, org: Organization
) -> None:
    if user.is_super_admin or _is_org_admin_of(user, org):
        return
    if await _is_group_admin(db, user, group):
        return
    raise HTTPException(
        status_code=403,
        detail=f"Only admin of group '{group.slug}' (or org admin / super_admin) may perform this",
    )


async def _require_group_member(
    db: AsyncSession, user: User, group: Group, org: Organization
) -> None:
    if user.is_super_admin or _is_org_admin_of(user, org):
        return
    if await _membership(db, user.id, group.id) is not None:
        return
    raise HTTPException(
        status_code=403, detail=f"Only members of group '{group.slug}' may read it"
    )


def _serialize_group(group: Group, org_slug: str, member_count: int) -> GroupOut:
    return GroupOut(
        id=group.id,
        slug=group.slug,
        name=group.name,
        org_slug=org_slug,
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
    # Pick the target org: explicit `org_slug` if super_admin specified it,
    # otherwise the caller's own org.
    if payload.org_slug:
        if not user.is_super_admin:
            raise HTTPException(
                status_code=403,
                detail="Only super_admin may create groups in an arbitrary org; "
                "leave org_slug blank to use your own org",
            )
        org = await _org_by_slug(db, payload.org_slug)
    else:
        if not user.org_id:
            raise HTTPException(
                status_code=400,
                detail="You don't belong to any org. Ask a super_admin to add you "
                "to one before creating a group.",
            )
        org = await db.get(Organization, user.org_id)
        if org is None:
            raise HTTPException(status_code=500, detail="Your org record is missing")

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
    # Creator gets first admin seat — unless they're super_admin acting on
    # someone else's org (then no auto-membership; they manage from outside).
    if user.org_id == org.id:
        db.add(UserGroupMembership(user_id=user.id, group_id=group.id, role="admin"))
    await db.commit()
    await db.refresh(group)
    return _serialize_group(group, org.slug, await _member_count(db, group.id))


@router.get("", response_model=list[GroupOut])
async def list_groups(user: CurrentUser, db: DBDep) -> list[GroupOut]:
    """List groups the caller can see.

    super_admin sees everything; an org member sees groups in their own org;
    a user without an org sees nothing.
    """
    if user.is_super_admin:
        stmt = (
            select(Group, Organization)
            .join(Organization, Organization.id == Group.org_id)
            .order_by(Group.created_at.desc())
        )
    elif user.org_id:
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
        _serialize_group(g, o.slug, await _member_count(db, g.id)) for (g, o) in rows
    ]


@router.get("/{slug}", response_model=GroupOut)
async def get_group(slug: str, user: CurrentUser, db: DBDep) -> GroupOut:
    group, org = await _group_by_slug(db, slug)
    await _require_group_member(db, user, group, org)
    return _serialize_group(group, org.slug, await _member_count(db, group.id))


@router.patch("/{slug}", response_model=GroupOut)
async def update_group(
    slug: str, payload: GroupUpdate, user: CurrentUser, db: DBDep
) -> GroupOut:
    group, org = await _group_by_slug(db, slug)
    await _require_group_admin(db, user, group, org)
    group.name = payload.name
    await db.commit()
    await db.refresh(group)
    return _serialize_group(group, org.slug, await _member_count(db, group.id))


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(slug: str, user: CurrentUser, db: DBDep) -> None:
    group, org = await _group_by_slug(db, slug)
    await _require_group_admin(db, user, group, org)
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
    await _require_group_admin(db, user, group, org)
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
    await _require_group_admin(db, user, group, org)
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
    # Allow self-leave without admin rights; otherwise require admin.
    if user_id != user.id:
        await _require_group_admin(db, user, group, org)
    m = await _membership(db, user_id, group.id)
    if m is None:
        raise HTTPException(
            status_code=404, detail=f"User '{user_id}' is not a member of group '{slug}'"
        )
    await db.delete(m)
    await db.commit()
    return None
