"""Bootstrap helpers run at app startup.

Right now there's exactly one: if `BOOTSTRAP_SUPER_ADMIN_EMAIL` is set and
that user already exists in the database but isn't yet super_admin,
promote them. This lets a fresh deploy get a known admin without an
out-of-band shell command.

Run from `lifespan` (idempotent — safe on every boot).
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memory_service.config import Settings
from memory_service.models import User

_logger = logging.getLogger(__name__)


async def promote_bootstrap_super_admin(settings: Settings, session: AsyncSession) -> None:
    """Idempotent promotion of `BOOTSTRAP_SUPER_ADMIN_EMAIL` to super admin."""
    email = (settings.bootstrap_super_admin_email or "").strip().lower()
    if not email:
        return

    stmt = select(User).where(User.email == email)
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user is None:
        _logger.info(
            "BOOTSTRAP_SUPER_ADMIN_EMAIL=%s — user does not exist yet, will be promoted on registration",
            email,
        )
        return
    if user.is_super_admin:
        return

    user.is_super_admin = True
    await session.commit()
    _logger.warning("Promoted existing user %s to super_admin (bootstrap)", email)


def is_bootstrap_super_admin(email: str, settings: Settings) -> bool:
    """Whether a newly-registering email should be auto-promoted."""
    target = (settings.bootstrap_super_admin_email or "").strip().lower()
    return bool(target) and email.strip().lower() == target
