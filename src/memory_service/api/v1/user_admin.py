"""/v1/users — admin-managed user CRUD.

This is the enterprise onboarding surface:

  POST   /v1/users                    create + return invite URL  (super | org_admin)
  GET    /v1/users                    list                         (super → all; org_admin → own org)
  GET    /v1/users/{id}               read                         (admin or self)
  PATCH  /v1/users/{id}               update role / activate / org  (admin)
  POST   /v1/users/{id}/resend-invite reissue invite               (admin)
  DELETE /v1/users/{id}               hard delete                  (super_admin only)

API-key endpoints under /v1/users/me/api-keys keep their old path
(handled by `api/v1/users.py`).
"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memory_service.api.deps import CurrentUser, DBDep
from memory_service.config import get_settings
from memory_service.models import (
    Group,
    Organization,
    User,
    UserGroupMembership,
)
from memory_service.core.auth import hash_password
from memory_service.schemas.user_mgmt import (
    CreateUserRequest,
    CreateUserResponse,
    InviteInfo,
    ResendInviteResponse,
    ResetPasswordRequest,
    ResetPasswordResponse,
    UpdateUserRequest,
    UserListItem,
    UserListResponse,
)

router = APIRouter(prefix="/users", tags=["users"])


# ---------- helpers ----------


def _gen_invite_token() -> str:
    return secrets.token_urlsafe(32)


async def _user_by_id_for_admin(db: AsyncSession, user_id: str) -> User:
    u = await db.get(User, user_id)
    if u is None:
        raise HTTPException(status_code=404, detail=f"User {user_id!r} not found")
    return u


async def _org_by_slug(db: AsyncSession, slug: str) -> Organization:
    org = (
        await db.execute(select(Organization).where(Organization.slug == slug))
    ).scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=404, detail=f"Org {slug!r} not found")
    return org


def _can_manage_user(actor: User, target_org_id: str | None) -> bool:
    """Returns True if `actor` is allowed to manage a user attached to
    `target_org_id` (None = unassigned). Org_admin can only touch their
    own org's users; super_admin can touch anyone."""
    if actor.is_super_admin:
        return True
    if actor.is_org_admin and actor.org_id and actor.org_id == target_org_id:
        return True
    return False


async def _serialize_user(db: AsyncSession, u: User) -> UserListItem:
    org_slug = None
    if u.org_id:
        org_slug = (
            await db.execute(select(Organization.slug).where(Organization.id == u.org_id))
        ).scalar_one_or_none()
    has_invite = u.invite_token is not None
    return UserListItem(
        id=u.id,
        email=u.email,
        username=u.username,
        display_name=u.display_name,
        org_slug=org_slug,
        is_super_admin=u.is_super_admin,
        is_org_admin=u.is_org_admin,
        is_active=u.is_active,
        has_password=u.password_hash is not None,
        has_pending_invite=has_invite,
        invite_expires_at=u.invite_token_expires_at if has_invite else None,
        created_at=u.created_at,
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _invite_expires() -> datetime:
    return _now() + timedelta(hours=get_settings().invite_ttl_hours)


# ---------- create ----------


@router.post(
    "", status_code=status.HTTP_201_CREATED, response_model=CreateUserResponse
)
async def create_user(
    payload: CreateUserRequest, actor: CurrentUser, db: DBDep,
) -> CreateUserResponse:
    # Determine target org_id (for permission check).
    target_org_id: str | None = None
    if payload.org_slug:
        org = await _org_by_slug(db, payload.org_slug)
        target_org_id = org.id

    if not _can_manage_user(actor, target_org_id):
        raise HTTPException(
            status_code=403,
            detail=(
                "Need super_admin (any org) or org_admin (own org only) to "
                "create users in that scope."
            ),
        )
    # Org_admin can't elevate someone to super_admin.
    if payload.is_super_admin and not actor.is_super_admin:
        raise HTTPException(
            status_code=403, detail="Only super_admin can grant super_admin",
        )

    existing = (
        await db.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"User {payload.email!r} already exists")

    # Validate groups (must belong to the same org).
    group_objs: list[Group] = []
    for slug in payload.group_slugs:
        g = (
            await db.execute(select(Group).where(Group.slug == slug))
        ).scalar_one_or_none()
        if g is None:
            raise HTTPException(status_code=404, detail=f"Group {slug!r} not found")
        if target_org_id is None or g.org_id != target_org_id:
            raise HTTPException(
                status_code=409,
                detail=f"Group {slug!r} is not in the target user's org",
            )
        group_objs.append(g)

    # Reject username collisions up-front for a friendlier error.
    requested_username = (payload.username or "").strip().lower() or None
    if requested_username:
        clash = (
            await db.execute(select(User).where(User.username == requested_username))
        ).scalar_one_or_none()
        if clash is not None:
            raise HTTPException(
                status_code=409, detail=f"Username {requested_username!r} already taken",
            )

    token = _gen_invite_token()
    expires_at = _invite_expires()
    u = User(
        email=payload.email,
        username=requested_username,
        display_name=payload.display_name,
        password_hash=None,  # set by accept-invite
        org_id=target_org_id,
        is_super_admin=payload.is_super_admin,
        is_org_admin=payload.is_org_admin,
        is_active=True,
        invite_token=token,
        invite_token_expires_at=expires_at,
        invited_by_user_id=actor.id,
    )
    db.add(u)
    await db.flush()
    for g in group_objs:
        db.add(UserGroupMembership(user_id=u.id, group_id=g.id, role="member"))
    await db.commit()
    await db.refresh(u)

    return CreateUserResponse(
        user=await _serialize_user(db, u),
        invite=InviteInfo(token=token, expires_at=expires_at),
    )


# ---------- list ----------


@router.get("", response_model=UserListResponse)
async def list_users(actor: CurrentUser, db: DBDep) -> UserListResponse:
    """super_admin sees everyone; org_admin sees their own org; regular
    users get 403 (they should use /v1/auth/me for self)."""
    if actor.is_super_admin:
        stmt = select(User).order_by(User.created_at.desc())
    elif actor.is_org_admin and actor.org_id:
        stmt = select(User).where(User.org_id == actor.org_id).order_by(User.created_at.desc())
    else:
        raise HTTPException(
            status_code=403, detail="List requires super_admin or org_admin",
        )
    rows = list((await db.execute(stmt)).scalars().all())
    return UserListResponse(users=[await _serialize_user(db, u) for u in rows])


# ---------- read ----------


@router.get("/by-id/{user_id}", response_model=UserListItem)
async def get_user(user_id: str, actor: CurrentUser, db: DBDep) -> UserListItem:
    u = await _user_by_id_for_admin(db, user_id)
    if actor.id != u.id and not _can_manage_user(actor, u.org_id):
        raise HTTPException(status_code=403, detail="Not allowed")
    return await _serialize_user(db, u)


# ---------- update ----------


@router.patch("/by-id/{user_id}", response_model=UserListItem)
async def update_user(
    user_id: str,
    payload: UpdateUserRequest,
    actor: CurrentUser,
    db: DBDep,
) -> UserListItem:
    u = await _user_by_id_for_admin(db, user_id)
    self_edit = actor.id == u.id
    admin_edit = _can_manage_user(actor, u.org_id)
    if not (self_edit or admin_edit):
        raise HTTPException(status_code=403, detail="Not allowed")

    if payload.display_name is not None:
        u.display_name = payload.display_name

    if payload.is_active is not None:
        if not admin_edit:
            raise HTTPException(status_code=403, detail="Only admins can change is_active")
        u.is_active = payload.is_active

    if payload.is_super_admin is not None:
        if not actor.is_super_admin:
            raise HTTPException(status_code=403, detail="Only super_admin can toggle super_admin")
        u.is_super_admin = payload.is_super_admin

    if payload.is_org_admin is not None:
        if not admin_edit:
            raise HTTPException(status_code=403, detail="Only admins can change is_org_admin")
        u.is_org_admin = payload.is_org_admin

    if payload.org_slug is not None:
        if not actor.is_super_admin:
            raise HTTPException(status_code=403, detail="Only super_admin can move users between orgs")
        if payload.org_slug == "":
            u.org_id = None
            u.is_org_admin = False
        else:
            org = await _org_by_slug(db, payload.org_slug)
            u.org_id = org.id

    await db.commit()
    await db.refresh(u)
    return await _serialize_user(db, u)


# ---------- resend invite ----------


@router.post("/by-id/{user_id}/resend-invite", response_model=ResendInviteResponse)
async def resend_invite(
    user_id: str, actor: CurrentUser, db: DBDep,
) -> ResendInviteResponse:
    u = await _user_by_id_for_admin(db, user_id)
    if not _can_manage_user(actor, u.org_id):
        raise HTTPException(status_code=403, detail="Not allowed")
    if u.password_hash is not None:
        raise HTTPException(
            status_code=409,
            detail=f"User has already set a password — invite reissue is for unaccepted invites only",
        )
    u.invite_token = _gen_invite_token()
    u.invite_token_expires_at = _invite_expires()
    await db.commit()
    return ResendInviteResponse(
        invite=InviteInfo(token=u.invite_token, expires_at=u.invite_token_expires_at)
    )


# ---------- reset password ----------


@router.post("/by-id/{user_id}/reset-password", response_model=ResetPasswordResponse)
async def reset_password(
    user_id: str,
    payload: ResetPasswordRequest,
    actor: CurrentUser,
    db: DBDep,
) -> ResetPasswordResponse:
    """Admin-initiated password reset.

    Two modes (see ResetPasswordRequest docstring). Either way the
    user lands with `must_change_password=true` — they'll be forced
    to pick their own on next login.
    """
    u = await _user_by_id_for_admin(db, user_id)
    if not _can_manage_user(actor, u.org_id):
        raise HTTPException(status_code=403, detail="Not allowed")
    if u.id == actor.id:
        raise HTTPException(
            status_code=409,
            detail=(
                "Use /v1/auth/change-password to change your own password "
                "(requires knowing the current one)."
            ),
        )

    if payload.new_password:
        u.password_hash = hash_password(payload.new_password)
        u.must_change_password = True
        # Stale invite tokens get cleared — direct-set means we won't
        # be using them.
        u.invite_token = None
        u.invite_token_expires_at = None
        await db.commit()
        return ResetPasswordResponse(mode="direct", new_password=payload.new_password)

    # Invite-link mode: keep current password (user might still know
    # it), issue token, force a change. Doesn't strand them if the URL
    # gets lost in transit.
    token = _gen_invite_token()
    expires_at = _invite_expires()
    u.invite_token = token
    u.invite_token_expires_at = expires_at
    u.must_change_password = True
    await db.commit()
    return ResetPasswordResponse(
        mode="invite_link",
        invite=InviteInfo(token=token, expires_at=expires_at),
    )


# ---------- delete ----------


@router.delete("/by-id/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: str, actor: CurrentUser, db: DBDep) -> None:
    if not actor.is_super_admin:
        raise HTTPException(status_code=403, detail="Only super_admin can hard-delete")
    if user_id == actor.id:
        raise HTTPException(status_code=409, detail="Refusing to delete yourself")
    u = await _user_by_id_for_admin(db, user_id)
    # Their personal user:<id> FalkorDB graph is left alone — no clean
    # way to reach it after the row is gone, and historical data has
    # audit value. Episodes' created_by_user_id FK has SET NULL.
    await db.delete(u)
    await db.commit()
