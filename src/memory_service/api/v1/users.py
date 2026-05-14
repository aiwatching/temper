"""/v1/users — currently scoped to `/me/api-keys`.

Org-level user admin endpoints land in Phase 1.3.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from memory_service.api.deps import CurrentUser, DBDep
from memory_service.core.auth import api_key_prefix, generate_api_key, hash_api_key
from memory_service.models import APIKey, User
from memory_service.schemas.api_key import (
    AdminAPIKeyListItem,
    APIKeyCreatedResponse,
    APIKeyResponse,
    APIKeyScopeUpdate,
    APIKeyUpdateRequest,
    CreateAPIKeyRequest,
)


_SLUG_INVALID_RE = re.compile(r"[^a-z0-9]+")


def _slugify_agent_name(name: str) -> str | None:
    """Best-effort agent_name → agent_slug. Mirrors the integrate.html
    JS so server and UI agree. Returns None when the result would be
    empty (e.g. an emoji-only name) — the caller falls back to a NULL
    agent_slug (legacy / unscoped) so creation still succeeds.
    """
    s = _SLUG_INVALID_RE.sub("-", name.lower()).strip("-")[:64].strip("-")
    return s or None

router = APIRouter(prefix="/users/me/api-keys", tags=["api-keys"])
admin_router = APIRouter(prefix="/admin/api-keys", tags=["api-keys"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=APIKeyCreatedResponse)
async def create_api_key(
    payload: CreateAPIKeyRequest,
    user: CurrentUser,
    db: DBDep,
) -> APIKeyCreatedResponse:
    """Create + return a new API key. **Plaintext is returned only here.**

    `agent_slug` (when given) becomes the key's routing scope: requests
    authed by this key default to namespace `agent:<user_id>/<slug>`.
    Two keys with the same slug share that scope on purpose; the DB
    unique constraint blocks accidental duplicates.
    """
    # When the caller didn't pin a slug, derive one from agent_name so every
    # new key is scoped by default (matches the integrate-page JS behaviour
    # but also covers raw API / memctl / curl creations).
    effective_slug = payload.agent_slug
    if effective_slug is None:
        effective_slug = _slugify_agent_name(payload.agent_name)
    plaintext = generate_api_key()
    api_key = APIKey(
        user_id=user.id,
        agent_name=payload.agent_name,
        agent_slug=effective_slug,
        key_hash=hash_api_key(plaintext),
        prefix=api_key_prefix(plaintext),
    )
    db.add(api_key)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        derived_hint = ""
        if payload.agent_slug is None and effective_slug is not None:
            derived_hint = (
                f" (slug was auto-derived from agent_name; pass "
                f"agent_slug explicitly to override)"
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"You already have an active key with agent_slug "
                f"{effective_slug!r}{derived_hint}. Revoke / rename the existing "
                "one, or pick a different slug — two keys with the same slug "
                "share memory, which is fine if that's what you want."
            ),
        ) from None
    await db.refresh(api_key)
    return APIKeyCreatedResponse(
        id=api_key.id,
        agent_name=api_key.agent_name,
        agent_slug=api_key.agent_slug,
        prefix=api_key.prefix,
        revoked=api_key.revoked,
        created_at=api_key.created_at,
        last_used_at=api_key.last_used_at,
        key=plaintext,
    )


@router.get("", response_model=list[APIKeyResponse])
async def list_api_keys(user: CurrentUser, db: DBDep) -> list[APIKey]:
    """List every API key the caller owns, including revoked ones."""
    stmt = select(APIKey).where(APIKey.user_id == user.id).order_by(APIKey.created_at.desc())
    return list((await db.execute(stmt)).scalars().all())


@router.patch("/{key_id}/scope", response_model=APIKeyResponse)
async def update_api_key_scope(
    key_id: str,
    payload: APIKeyScopeUpdate,
    user: CurrentUser,
    db: DBDep,
) -> APIKey:
    """Owner rebinds this key's agent_slug.

    Send `{"agent_slug": null}` to clear the scope (key becomes legacy /
    unscoped — its writes go to flat user:<id>). Send a slug to switch.
    Key plaintext is unchanged; existing agents holding the key keep
    working, but their future writes/reads route to the new namespace.

    Data already written under the old slug stays where it is. The
    default cross-agent search still surfaces it because
    `readable_namespaces_for()` enumerates every slug ever attached to
    one of your keys.
    """
    stmt = select(APIKey).where(APIKey.id == key_id, APIKey.user_id == user.id)
    api_key = (await db.execute(stmt)).scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    api_key.agent_slug = payload.agent_slug
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"You already have another key with agent_slug "
                f"{payload.agent_slug!r}. Two keys sharing a slug is allowed "
                "(that's how memory sharing works), but the DB constraint "
                "blocks duplicates — revoke / rename the existing one first."
            ),
        ) from None
    await db.refresh(api_key)
    return api_key


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(key_id: str, user: CurrentUser, db: DBDep) -> None:
    """Mark a key revoked. The row is kept for audit; future auths reject it."""
    stmt = select(APIKey).where(APIKey.id == key_id, APIKey.user_id == user.id)
    api_key = (await db.execute(stmt)).scalar_one_or_none()
    if api_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    if api_key.revoked:
        return None
    api_key.revoked = True
    await db.commit()
    return None


# ---------- admin: cross-user API key visibility ----------


@admin_router.get("", response_model=list[AdminAPIKeyListItem])
async def admin_list_all_api_keys(
    user: CurrentUser, db: DBDep,
) -> list[AdminAPIKeyListItem]:
    """super_admin sees every API key in the system + the owning user."""
    if not user.is_super_admin:
        raise HTTPException(status_code=403, detail="Only super_admin")
    stmt = (
        select(APIKey, User)
        .join(User, User.id == APIKey.user_id)
        .order_by(APIKey.created_at.desc())
    )
    rows = list((await db.execute(stmt)).all())
    return [
        AdminAPIKeyListItem(
            id=k.id,
            agent_name=k.agent_name,
            agent_slug=k.agent_slug,
            prefix=k.prefix,
            revoked=k.revoked,
            created_at=k.created_at,
            last_used_at=k.last_used_at,
            user_id=u.id,
            user_email=u.email,
            user_username=u.username,
        )
        for (k, u) in rows
    ]


@admin_router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_revoke_api_key(key_id: str, user: CurrentUser, db: DBDep) -> None:
    """super_admin revokes any user's key. Idempotent; row stays for audit."""
    if not user.is_super_admin:
        raise HTTPException(status_code=403, detail="Only super_admin")
    api_key = await db.get(APIKey, key_id)
    if api_key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    if api_key.revoked:
        return None
    api_key.revoked = True
    await db.commit()
    return None


@admin_router.patch("/{key_id}", response_model=AdminAPIKeyListItem)
async def admin_set_api_key_revoked(
    key_id: str,
    payload: APIKeyUpdateRequest,
    user: CurrentUser,
    db: DBDep,
) -> AdminAPIKeyListItem:
    """super_admin toggles revoked on/off. Use this to re-enable a key
    that was previously disabled — the plaintext is unchanged, so any
    agent still holding it works again on the next request."""
    if not user.is_super_admin:
        raise HTTPException(status_code=403, detail="Only super_admin")
    row = (
        await db.execute(
            select(APIKey, User)
            .join(User, User.id == APIKey.user_id)
            .where(APIKey.id == key_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found")
    api_key, owner = row
    api_key.revoked = payload.revoked
    await db.commit()
    await db.refresh(api_key)
    return AdminAPIKeyListItem(
        id=api_key.id,
        agent_name=api_key.agent_name,
        agent_slug=api_key.agent_slug,
        prefix=api_key.prefix,
        revoked=api_key.revoked,
        created_at=api_key.created_at,
        last_used_at=api_key.last_used_at,
        user_id=owner.id,
        user_email=owner.email,
        user_username=owner.username,
    )
