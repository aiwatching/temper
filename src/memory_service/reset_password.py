"""Server-side password reset.

For when an operator has lost the password to their account (or a
user's) and there's no live admin session to do it through the UI.
Touches the DB directly — runs against whatever DATABASE_URL the
service is configured for.

Two modes:

  --new-password X   Set the password directly; the user must change
                     it on next login (must_change_password=true).
  (default)          Generate a one-time invite token; user follows
                     the link to set their own password. Safer — the
                     operator never sees the plaintext.

Usage:
    python -m memory_service.reset_password EMAIL
    python -m memory_service.reset_password EMAIL --new-password 'temporary'
"""
from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import select


async def _main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("email", help="Account whose password to reset")
    ap.add_argument(
        "--new-password",
        help="Set this password directly. User will be forced to change on next login. "
        "Omit to generate an invite-style token instead.",
    )
    ap.add_argument(
        "--invite-base-url",
        default="http://localhost:8000",
        help="Used to build the printable invite URL (default localhost).",
    )
    args = ap.parse_args()

    # Late imports so `python -m memory_service.reset_password --help` works
    # without DB connection / config.
    from memory_service.config import get_settings
    from memory_service.core.auth import hash_password
    from memory_service.db.session import init_database
    from memory_service.models import User

    settings = get_settings()
    db = init_database(settings)
    email = args.email.strip().lower()

    async for session in db.session():
        user = (
            await session.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if user is None:
            print(f"  no user with email {email!r}", file=sys.stderr)
            return 1

        if args.new_password:
            user.password_hash = hash_password(args.new_password)
            user.must_change_password = True
            user.invite_token = None
            user.invite_token_expires_at = None
            await session.commit()
            print(f"  ✓ password for {email} set to: {args.new_password}")
            print(f"  user will be forced to change it on next login.")
        else:
            token = secrets.token_urlsafe(32)
            expires_at = datetime.now(UTC) + timedelta(hours=settings.invite_ttl_hours)
            user.invite_token = token
            user.invite_token_expires_at = expires_at
            # Don't clear password_hash — they keep current creds in case
            # the URL gets lost, BUT marking must_change_password=true so
            # if they happen to remember the old one they're still forced
            # to update.
            user.must_change_password = True
            await session.commit()
            url = f"{args.invite_base_url.rstrip('/')}/admin/accept-invite?token={token}"
            print(f"  ✓ generated reset link for {email}")
            print(f"  send them: {url}")
            print(f"  expires at: {expires_at.isoformat()}")
        break

    await db.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
