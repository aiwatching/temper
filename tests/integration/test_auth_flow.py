"""End-to-end auth + API key flow.

Hits the live FastAPI app (via ASGITransport) so this exercises the same
code path a real curl call would.
"""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_register_login_me_roundtrip(client) -> None:  # type: ignore[no-untyped-def]
    # Register
    r = await client.post(
        "/v1/auth/register",
        json={"email": "jerry@example.com", "password": "correct horse battery", "display_name": "Jerry"},
    )
    assert r.status_code == 201, r.text
    user = r.json()
    assert user["email"] == "jerry@example.com"
    assert user["display_name"] == "Jerry"
    assert "password" not in user and "password_hash" not in user

    # Login
    r = await client.post(
        "/v1/auth/login",
        json={"email": "jerry@example.com", "password": "correct horse battery"},
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]

    # /me with bearer
    r = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "jerry@example.com"

    # /me without token -> 401
    r = await client.get("/v1/auth/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_register_duplicate_email_conflict(client) -> None:  # type: ignore[no-untyped-def]
    payload = {"email": "dup@example.com", "password": "pw-correct-horse-battery"}
    r1 = await client.post("/v1/auth/register", json=payload)
    assert r1.status_code == 201
    r2 = await client.post("/v1/auth/register", json=payload)
    assert r2.status_code == 409


@pytest.mark.asyncio
async def test_login_wrong_password(client) -> None:  # type: ignore[no-untyped-def]
    await client.post(
        "/v1/auth/register",
        json={"email": "bad@example.com", "password": "correct-horse-battery"},
    )
    r = await client.post(
        "/v1/auth/login",
        json={"email": "bad@example.com", "password": "wrong-password-123"},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_api_key_lifecycle(client) -> None:  # type: ignore[no-untyped-def]
    # Register + login
    await client.post(
        "/v1/auth/register",
        json={"email": "agent-owner@example.com", "password": "pw-pw-pw-pw"},
    )
    r = await client.post(
        "/v1/auth/login",
        json={"email": "agent-owner@example.com", "password": "pw-pw-pw-pw"},
    )
    token = r.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    # Create API key
    r = await client.post(
        "/v1/users/me/api-keys",
        json={"agent_name": "english-agent"},
        headers=auth,
    )
    assert r.status_code == 201, r.text
    created = r.json()
    key_plain = created["key"]
    key_id = created["id"]
    assert key_plain.startswith("mk_")
    assert created["prefix"].startswith("mk_")
    assert created["agent_name"] == "english-agent"

    # /me via API key (no Bearer)
    r = await client.get("/v1/auth/me", headers={"X-API-Key": key_plain})
    assert r.status_code == 200
    assert r.json()["email"] == "agent-owner@example.com"

    # List keys
    r = await client.get("/v1/users/me/api-keys", headers=auth)
    assert r.status_code == 200
    keys = r.json()
    assert len(keys) == 1
    assert keys[0]["id"] == key_id
    assert "key" not in keys[0]  # plaintext never leaks after creation

    # Revoke
    r = await client.delete(f"/v1/users/me/api-keys/{key_id}", headers=auth)
    assert r.status_code == 204

    # Revoked key is rejected
    r = await client.get("/v1/auth/me", headers={"X-API-Key": key_plain})
    assert r.status_code == 401

    # Listing still shows the key, but revoked=True
    r = await client.get("/v1/users/me/api-keys", headers=auth)
    assert r.status_code == 200
    assert r.json()[0]["revoked"] is True


@pytest.mark.asyncio
async def test_api_key_revoke_not_owned(client) -> None:  # type: ignore[no-untyped-def]
    """User A cannot revoke User B's key — should 404."""
    # User A creates a key
    await client.post(
        "/v1/auth/register",
        json={"email": "a@example.com", "password": "pwpwpwpw"},
    )
    r = await client.post(
        "/v1/auth/login",
        json={"email": "a@example.com", "password": "pwpwpwpw"},
    )
    token_a = r.json()["access_token"]
    r = await client.post(
        "/v1/users/me/api-keys",
        json={"agent_name": "a-agent"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    key_id = r.json()["id"]

    # User B tries to revoke it
    await client.post(
        "/v1/auth/register",
        json={"email": "b@example.com", "password": "pwpwpwpw"},
    )
    r = await client.post(
        "/v1/auth/login",
        json={"email": "b@example.com", "password": "pwpwpwpw"},
    )
    token_b = r.json()["access_token"]
    r = await client.delete(
        f"/v1/users/me/api-keys/{key_id}",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert r.status_code == 404
