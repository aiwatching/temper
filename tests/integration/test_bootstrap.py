"""Bootstrap super admin promotion paths.

Covers both:
- new registration whose email matches BOOTSTRAP_SUPER_ADMIN_EMAIL
- existing user being promoted on the next app startup
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_register_promotes_when_email_matches(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from memory_service.config import get_settings

    monkeypatch.setenv("BOOTSTRAP_SUPER_ADMIN_EMAIL", "boss@example.com")
    get_settings.cache_clear()

    r = await client.post(
        "/v1/auth/register",
        json={"email": "boss@example.com", "password": "correct-horse-battery"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["is_super_admin"] is True


@pytest.mark.asyncio
async def test_register_does_not_promote_other_emails(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from memory_service.config import get_settings

    monkeypatch.setenv("BOOTSTRAP_SUPER_ADMIN_EMAIL", "boss@example.com")
    get_settings.cache_clear()

    r = await client.post(
        "/v1/auth/register",
        json={"email": "regular@example.com", "password": "correct-horse-battery"},
    )
    assert r.status_code == 201
    assert r.json()["is_super_admin"] is False


@pytest.mark.asyncio
async def test_promote_existing_user_on_bootstrap(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """User registered before BOOTSTRAP env var was set → promoted on next boot."""
    from memory_service.config import get_settings
    from memory_service.core.bootstrap import promote_bootstrap_super_admin
    from memory_service.db.session import get_database

    # 1. Register without the env var set
    monkeypatch.delenv("BOOTSTRAP_SUPER_ADMIN_EMAIL", raising=False)
    get_settings.cache_clear()
    r = await client.post(
        "/v1/auth/register",
        json={"email": "later@example.com", "password": "correct-horse-battery"},
    )
    assert r.status_code == 201
    assert r.json()["is_super_admin"] is False

    # 2. Now set the env var (simulating a config change between boots) and
    #    invoke the bootstrap routine.
    monkeypatch.setenv("BOOTSTRAP_SUPER_ADMIN_EMAIL", "later@example.com")
    get_settings.cache_clear()
    db = get_database()
    async for session in db.session():
        await promote_bootstrap_super_admin(get_settings(), session)
        break

    # 3. The next login / /me call should see is_super_admin = True
    r = await client.post(
        "/v1/auth/login",
        json={"email": "later@example.com", "password": "correct-horse-battery"},
    )
    token = r.json()["access_token"]
    r = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["is_super_admin"] is True
