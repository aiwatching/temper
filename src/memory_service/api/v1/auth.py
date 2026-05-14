"""/v1/auth — register, login, current user, invite acceptance."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from memory_service.api.deps import CurrentUser, DBDep, SettingsDep
from memory_service.core.auth import (
    hash_password,
    issue_session_token,
    verify_password,
)
from memory_service.core.bootstrap import is_bootstrap_super_admin
from memory_service.models import User
from memory_service.schemas.auth import (
    AcceptInviteRequest,
    ChangePasswordRequest,
    InitialAdminRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserResponse)
async def register(
    payload: RegisterRequest, db: DBDep, settings: SettingsDep
) -> User:
    """Create a new user account via self-registration.

    Disabled when ALLOW_SELF_REGISTRATION=false (production default) —
    in that mode, admins onboard users via POST /v1/users instead.
    """
    if not settings.allow_self_registration:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Self-registration is disabled on this server. "
                "Ask an admin to invite you via POST /v1/users."
            ),
        )
    email = str(payload.email).lower()
    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
    )
    if is_bootstrap_super_admin(email, settings):
        user.is_super_admin = True
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


@router.post(
    "/setup/initial-admin",
    status_code=status.HTTP_201_CREATED,
    response_model=UserResponse,
)
async def initial_admin_setup(
    payload: InitialAdminRequest, db: DBDep
) -> User:
    """One-shot setup endpoint: create the first super_admin.

    Only works while the users table is empty. After the first user
    exists, returns 409 — admin management goes through /v1/users from
    then on. This is the "fresh deploy, no env var" onboarding path.
    """
    count = (await db.execute(select(func.count(User.id)))).scalar_one()
    if count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Initial-admin setup already done; use the admin UI or "
                "POST /v1/users to add more users."
            ),
        )
    email = str(payload.email).lower()
    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
        is_super_admin=True,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.post(
    "/accept-invite",
    response_model=TokenResponse,
)
async def accept_invite(
    payload: AcceptInviteRequest, db: DBDep, settings: SettingsDep
) -> TokenResponse:
    """User clicks the invite URL → sets a password → gets a session
    token (auto-login). Token is single-use: cleared on success.
    """
    user = (
        await db.execute(select(User).where(User.invite_token == payload.token))
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="Invalid or already-used invite token")
    # SQLite drops tzinfo on round-trip; normalize before comparing.
    if user.invite_token_expires_at is not None:
        expires = user.invite_token_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires < datetime.now(UTC):
            raise HTTPException(status_code=410, detail="Invite token expired — ask admin to resend")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")
    user.password_hash = hash_password(payload.password)
    user.invite_token = None
    user.invite_token_expires_at = None
    if payload.display_name and not user.display_name:
        user.display_name = payload.display_name
    await db.commit()
    await db.refresh(user)
    token, expires_at = issue_session_token(user.id, settings)
    return TokenResponse(access_token=token, expires_at=expires_at)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: DBDep, settings: SettingsDep) -> TokenResponse:
    # Identifier can be either an email ("alice@acme.com") or a short
    # username ("admin"). We dispatch on the presence of `@`.
    identifier = payload.email.strip().lower()
    if "@" in identifier:
        stmt = select(User).where(User.email == identifier)
    else:
        stmt = select(User).where(User.username == identifier)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if (
        not user
        or not user.is_active
        or user.password_hash is None
        or not verify_password(payload.password, user.password_hash)
    ):
        # Generic message — don't leak which half is wrong, or that the
        # user exists but hasn't accepted their invite yet.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email/username or password",
        )
    token, expires_at = issue_session_token(user.id, settings)
    return TokenResponse(access_token=token, expires_at=expires_at)


@router.post(
    "/change-password", status_code=status.HTTP_204_NO_CONTENT
)
async def change_password(
    payload: ChangePasswordRequest, user: CurrentUser, db: DBDep
) -> None:
    """Self-service password change.

    Requires the current password (defends against session hijack
    setting a new password the legit user doesn't know). Clears
    `must_change_password` on success so the UI lets the user proceed.
    """
    if user.password_hash is None or not verify_password(
        payload.old_password, user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Current password is incorrect",
        )
    if payload.new_password == payload.old_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must differ from current",
        )
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    await db.commit()
    return None


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
