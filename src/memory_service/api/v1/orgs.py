"""/v1/orgs — organization CRUD + members.

Org creation is super_admin-only by design: org slugs become public
namespace prefixes (`org:<slug>`) and self-service invites squatting.
Membership is one-org-per-user, enforced by `User.org_id` being a single
FK. `is_org_admin` is a per-user bool, scoped implicitly to that user's
current org — change orgs, lose the role.
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
from memory_service.schemas.org import (
    OrgCreate,
    OrgMemberAdd,
    OrgMemberOut,
    OrgMemberRoleUpdate,
    OrgOut,
    OrgUpdate,
)

router = APIRouter(prefix="/orgs", tags=["orgs"])


# ---------- helpers ----------


async def _org_by_slug(db: AsyncSession, slug: str) -> Organization:
    org = (
        await db.execute(select(Organization).where(Organization.slug == slug))
    ).scalar_one_or_none()
    if org is None:
        # We give 404 here even to non-admins — slug existence is not a
        # secret (one is published in every member's `user.org_id`-derived
        # namespace).
        raise HTTPException(status_code=404, detail=f"Org '{slug}' not found")
    return org


async def _member_count(db: AsyncSession, org_id: str) -> int:
    return int(
        (
            await db.execute(
                select(func.count(User.id)).where(User.org_id == org_id)
            )
        ).scalar_one()
    )


def _is_org_admin_of(user: User, org: Organization) -> bool:
    """True if `user` is admin of THIS specific org."""
    return user.org_id == org.id and user.is_org_admin


async def _require_org_admin(user: User, org: Organization) -> None:
    if user.is_super_admin or _is_org_admin_of(user, org):
        return
    raise HTTPException(
        status_code=403,
        detail=f"Only super_admin or admin of org '{org.slug}' may perform this action",
    )


async def _require_org_member(user: User, org: Organization) -> None:
    if user.is_super_admin or user.org_id == org.id:
        return
    raise HTTPException(
        status_code=403,
        detail=f"Only members of org '{org.slug}' may read it",
    )


def _serialize_org(org: Organization, member_count: int) -> OrgOut:
    return OrgOut(
        id=org.id,
        slug=org.slug,
        name=org.name,
        created_at=org.created_at,
        member_count=member_count,
    )


# ---------- org CRUD ----------


@router.post("", status_code=status.HTTP_201_CREATED, response_model=OrgOut)
async def create_org(payload: OrgCreate, user: CurrentUser, db: DBDep) -> OrgOut:
    if not user.is_super_admin:
        raise HTTPException(status_code=403, detail="Only super_admin can create orgs")
    existing = (
        await db.execute(select(Organization).where(Organization.slug == payload.slug))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Org slug '{payload.slug}' already taken")
    org = Organization(slug=payload.slug, name=payload.name)
    db.add(org)
    await db.commit()
    await db.refresh(org)
    return _serialize_org(org, member_count=0)


@router.get("", response_model=list[OrgOut])
async def list_orgs(user: CurrentUser, db: DBDep) -> list[OrgOut]:
    if user.is_super_admin:
        stmt = select(Organization).order_by(Organization.created_at.desc())
    elif user.org_id:
        stmt = select(Organization).where(Organization.id == user.org_id)
    else:
        return []
    rows = list((await db.execute(stmt)).scalars().all())
    return [_serialize_org(o, await _member_count(db, o.id)) for o in rows]


@router.get("/{slug}", response_model=OrgOut)
async def get_org(slug: str, user: CurrentUser, db: DBDep) -> OrgOut:
    org = await _org_by_slug(db, slug)
    await _require_org_member(user, org)
    return _serialize_org(org, await _member_count(db, org.id))


@router.patch("/{slug}", response_model=OrgOut)
async def update_org(
    slug: str, payload: OrgUpdate, user: CurrentUser, db: DBDep
) -> OrgOut:
    org = await _org_by_slug(db, slug)
    if not user.is_super_admin:
        raise HTTPException(status_code=403, detail="Only super_admin can rename orgs")
    org.name = payload.name
    await db.commit()
    await db.refresh(org)
    return _serialize_org(org, await _member_count(db, org.id))


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_org(slug: str, user: CurrentUser, db: DBDep) -> None:
    org = await _org_by_slug(db, slug)
    if not user.is_super_admin:
        raise HTTPException(status_code=403, detail="Only super_admin can delete orgs")
    # FK ondelete='SET NULL' on User.org_id clears membership automatically;
    # groups inside the org cascade-delete via FK; group memberships cascade
    # from there.
    await db.delete(org)
    await db.commit()
    return None


# ---------- members ----------


@router.post(
    "/{slug}/members", status_code=status.HTTP_201_CREATED, response_model=OrgMemberOut
)
async def add_member(
    slug: str, payload: OrgMemberAdd, user: CurrentUser, db: DBDep
) -> OrgMemberOut:
    org = await _org_by_slug(db, slug)
    await _require_org_admin(user, org)
    target = await db.get(User, payload.user_id)
    if target is None:
        raise HTTPException(status_code=404, detail=f"User '{payload.user_id}' not found")
    if target.org_id is not None and target.org_id != org.id:
        raise HTTPException(
            status_code=409,
            detail=f"User already belongs to another org; remove them first",
        )
    target.org_id = org.id
    target.is_org_admin = payload.is_org_admin
    await db.commit()
    await db.refresh(target)
    return OrgMemberOut(
        user_id=target.id,
        email=target.email,
        display_name=target.display_name,
        is_org_admin=target.is_org_admin,
    )


@router.get("/{slug}/members", response_model=list[OrgMemberOut])
async def list_members(slug: str, user: CurrentUser, db: DBDep) -> list[OrgMemberOut]:
    org = await _org_by_slug(db, slug)
    await _require_org_member(user, org)
    rows = list(
        (
            await db.execute(
                select(User)
                .where(User.org_id == org.id)
                .order_by(User.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        OrgMemberOut(
            user_id=u.id,
            email=u.email,
            display_name=u.display_name,
            is_org_admin=u.is_org_admin,
        )
        for u in rows
    ]


@router.patch("/{slug}/members/{user_id}", response_model=OrgMemberOut)
async def update_member_role(
    slug: str,
    user_id: str,
    payload: OrgMemberRoleUpdate,
    user: CurrentUser,
    db: DBDep,
) -> OrgMemberOut:
    org = await _org_by_slug(db, slug)
    await _require_org_admin(user, org)
    target = await db.get(User, user_id)
    if target is None or target.org_id != org.id:
        raise HTTPException(
            status_code=404, detail=f"User '{user_id}' is not a member of org '{slug}'"
        )
    target.is_org_admin = payload.is_org_admin
    await db.commit()
    await db.refresh(target)
    return OrgMemberOut(
        user_id=target.id,
        email=target.email,
        display_name=target.display_name,
        is_org_admin=target.is_org_admin,
    )


@router.delete("/{slug}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    slug: str, user_id: str, user: CurrentUser, db: DBDep
) -> None:
    org = await _org_by_slug(db, slug)
    # Allow self-leave; otherwise require admin.
    if user_id != user.id:
        await _require_org_admin(user, org)
    target = await db.get(User, user_id)
    if target is None or target.org_id != org.id:
        raise HTTPException(
            status_code=404, detail=f"User '{user_id}' is not a member of org '{slug}'"
        )
    # Also clear the user's group memberships in groups owned by this org —
    # otherwise they'd retain group: namespace read access after losing their
    # org: read access, which is an inconsistent permissions footprint.
    await db.execute(
        UserGroupMembership.__table__.delete().where(
            UserGroupMembership.user_id == user_id,
            UserGroupMembership.group_id.in_(
                select(Group.id).where(Group.org_id == org.id)
            ),
        )
    )
    target.org_id = None
    target.is_org_admin = False
    await db.commit()
    return None
