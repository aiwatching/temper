"""/v1/users — currently scoped to `/me/api-keys`.

Org-level user admin endpoints land in Phase 1.3.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from memory_service.api.deps import CurrentUser, DBDep
from memory_service.core.auth import api_key_prefix, generate_api_key, hash_api_key
from memory_service.models import APIKey
from memory_service.schemas.api_key import (
    APIKeyCreatedResponse,
    APIKeyResponse,
    CreateAPIKeyRequest,
)

router = APIRouter(prefix="/users/me/api-keys", tags=["api-keys"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=APIKeyCreatedResponse)
async def create_api_key(
    payload: CreateAPIKeyRequest,
    user: CurrentUser,
    db: DBDep,
) -> APIKeyCreatedResponse:
    """Create + return a new API key. **Plaintext is returned only here.**"""
    plaintext = generate_api_key()
    api_key = APIKey(
        user_id=user.id,
        agent_name=payload.agent_name,
        key_hash=hash_api_key(plaintext),
        prefix=api_key_prefix(plaintext),
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    return APIKeyCreatedResponse(
        id=api_key.id,
        agent_name=api_key.agent_name,
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
