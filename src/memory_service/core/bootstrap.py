"""Bootstrap helpers run at app startup.

Right now there's exactly one: if `BOOTSTRAP_SUPER_ADMIN_EMAIL` is set and
that user already exists in the database but isn't yet super_admin,
promote them. This lets a fresh deploy get a known admin without an
out-of-band shell command.

Run from `lifespan` (idempotent — safe on every boot).
"""
from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from memory_service.config import Settings
from memory_service.core.auth import hash_password
from memory_service.models import User

_logger = logging.getLogger(__name__)


async def create_default_admin_if_empty(
    settings: Settings, session: AsyncSession
) -> None:
    """If `create_default_admin` is True AND the users table is empty,
    seed the configured default super_admin with `must_change_password`
    set so the operator is forced to pick their own password on first
    login. Idempotent — does nothing once any user exists.

    Logs the default credentials prominently (WARNING level) so a fresh
    deploy doesn't leave the operator guessing.
    """
    if not settings.create_default_admin:
        return
    count = (await session.execute(select(func.count(User.id)))).scalar_one()
    if count > 0:
        return

    email = settings.default_admin_email.strip().lower()
    username = settings.default_admin_username.strip().lower() or None
    user = User(
        email=email,
        username=username,
        password_hash=hash_password(settings.default_admin_password),
        display_name="Default Admin",
        is_super_admin=True,
        is_active=True,
        must_change_password=True,
    )
    session.add(user)
    await session.commit()
    _logger.warning(
        "Seeded default super_admin: username=%s (email=%s) password=%s — "
        "CHANGE THIS IMMEDIATELY on first login (UI will force you to).",
        username or "(none)",
        email,
        settings.default_admin_password,
    )


async def promote_bootstrap_super_admin(settings: Settings, session: AsyncSession) -> None:
    """One-shot promotion of `BOOTSTRAP_SUPER_ADMIN_EMAIL` to super_admin.

    Only acts when the system has zero super_admins — otherwise it becomes
    an every-restart override that silently re-promotes a user who was
    intentionally demoted (or who happened to register with that email).
    """
    email = (settings.bootstrap_super_admin_email or "").strip().lower()
    if not email:
        return

    existing_admin = (
        await session.execute(
            select(func.count(User.id)).where(User.is_super_admin.is_(True))
        )
    ).scalar_one()
    if existing_admin > 0:
        return

    stmt = select(User).where(User.email == email)
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user is None:
        _logger.info(
            "BOOTSTRAP_SUPER_ADMIN_EMAIL=%s — user does not exist yet, will be promoted on registration",
            email,
        )
        return

    user.is_super_admin = True
    await session.commit()
    _logger.warning("Promoted existing user %s to super_admin (bootstrap)", email)


async def is_bootstrap_super_admin(
    email: str, settings: Settings, session: AsyncSession
) -> bool:
    """Whether a newly-registering email should be auto-promoted.

    Like `promote_bootstrap_super_admin`, this is one-shot: once any
    super_admin exists, this returns False so a regular user registering
    with the configured email doesn't accidentally inherit admin rights.
    """
    target = (settings.bootstrap_super_admin_email or "").strip().lower()
    if not target or email.strip().lower() != target:
        return False
    existing_admin = (
        await session.execute(
            select(func.count(User.id)).where(User.is_super_admin.is_(True))
        )
    ).scalar_one()
    return existing_admin == 0
