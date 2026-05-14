"""Shared FastAPI dependencies — DB session + current-user resolution.

`get_current_user` accepts authentication via either:
  - `Authorization: Bearer <session-jwt>`  (from /v1/auth/login)
  - `X-API-Key: mk_...`                    (from /v1/users/me/api-keys)

If both are present, the API key wins (it's the agent path; tighter scope).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memory_service.config import Settings, get_settings
from memory_service.core.auth import decode_session_token, hash_api_key
from memory_service.db.session import get_database
from memory_service.models import APIKey, User


async def db_session() -> AsyncIterator[AsyncSession]:
    async for session in get_database().session():
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings)]
DBDep = Annotated[AsyncSession, Depends(db_session)]


async def _user_from_session(token: str, settings: Settings, db: AsyncSession) -> User | None:
    user_id = decode_session_token(token, settings)
    if not user_id:
        return None
    user = await db.get(User, user_id)
    return user if user and user.is_active else None


async def _user_from_api_key(key: str, db: AsyncSession) -> User | None:
    digest = hash_api_key(key)
    stmt = (
        select(APIKey, User)
        .join(User, User.id == APIKey.user_id)
        .where(APIKey.key_hash == digest, APIKey.revoked.is_(False), User.is_active.is_(True))
    )
    row = (await db.execute(stmt)).first()
    if row is None:
        return None
    api_key, user = row
    # Touch last_used_at (best-effort — failure here shouldn't break auth)
    api_key.last_used_at = datetime.now(UTC)
    # Stash the key's agent_slug on the User instance so default namespace
    # resolution (core.namespaces.default_namespace_for) picks it up. Set
    # to None explicitly for legacy keys so a stale attr from a previous
    # request can't leak across.
    user._default_agent_slug = api_key.agent_slug  # type: ignore[attr-defined]
    return user


async def get_current_user(
    db: DBDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> User:
    if x_api_key:
        user = await _user_from_api_key(x_api_key, db)
        if user:
            return user

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        user = await _user_from_session(token, settings, db)
        if user:
            return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


CurrentUser = Annotated[User, Depends(get_current_user)]
