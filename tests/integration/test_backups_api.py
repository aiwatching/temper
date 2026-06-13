"""Full-backups admin API: super_admin gate + list + download 404.

The pg_dump path needs a real Postgres + pg_dump binary, so we don't
exercise an actual backup here — that's covered manually. These cover
the auth gate and the read endpoints, which is where the security
matters (super_admin-only, no path traversal).
"""
from __future__ import annotations

from sqlalchemy import update

import pytest


async def _register(client, email: str) -> str:
    await client.post(
        "/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery-staple"},
    )
    r = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": "correct-horse-battery-staple"},
    )
    return r.json()["access_token"]


async def _promote(email: str) -> None:
    from memory_service.config import get_settings
    from memory_service.db.session import init_database
    from memory_service.models import User

    db = init_database(get_settings())
    async for s in db.session():
        await s.execute(update(User).where(User.email == email).values(is_super_admin=True))
        await s.commit()
        break


@pytest.mark.asyncio
async def test_backups_super_admin_only(client) -> None:  # type: ignore[no-untyped-def]
    plain = await _register(client, "plain@example.com")
    h = {"Authorization": f"Bearer {plain}"}

    assert (await client.get("/v1/admin/backups", headers=h)).status_code == 403
    assert (await client.post("/v1/admin/backups", headers=h)).status_code == 403
    assert (await client.get(
        "/v1/admin/backups/x/download/postgres", headers=h
    )).status_code == 403


@pytest.mark.asyncio
async def test_backups_list_empty_for_admin(client) -> None:  # type: ignore[no-untyped-def]
    tok = await _register(client, "admin@example.com")
    await _promote("admin@example.com")
    h = {"Authorization": f"Bearer {tok}"}

    r = await client.get("/v1/admin/backups", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"backups": []}

    # Download of a non-existent backup → 404 (not a path-traversal leak).
    r = await client.get("/v1/admin/backups/nope/download/postgres", headers=h)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_backups_require_auth(client) -> None:  # type: ignore[no-untyped-def]
    assert (await client.get("/v1/admin/backups")).status_code == 401
    assert (await client.post("/v1/admin/backups")).status_code == 401
