"""/v1/auth — register, login, current user."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from memory_service.api.deps import CurrentUser, DBDep, SettingsDep
from memory_service.core.auth import (
    hash_password,
    issue_session_token,
    verify_password,
)
from memory_service.models import User
from memory_service.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserResponse)
async def register(payload: RegisterRequest, db: DBDep) -> User:
    """Create a new user account. Returns the created user (no token).

    The first registered user with `BOOTSTRAP_SUPER_ADMIN_EMAIL` matching their
    address is auto-promoted to super admin — convenience for fresh installs.
    """
    user = User(
        email=str(payload.email).lower(),
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        ) from None
    await db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: DBDep, settings: SettingsDep) -> TokenResponse:
    stmt = select(User).where(User.email == str(payload.email).lower())
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        # Generic message — don't leak which half is wrong.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    token, expires_at = issue_session_token(user.id, settings)
    return TokenResponse(access_token=token, expires_at=expires_at)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout() -> None:
    """Stateless logout — JWTs aren't revocable server-side in this MVP.

    Clients should drop the token. Future iteration could maintain a
    revoked-jti table if needed.
    """
    return None


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser) -> User:
    return user
