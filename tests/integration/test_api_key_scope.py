"""API key scope: multiple keys may share a slug; slugs are sanitized
rather than rejected.
"""
from __future__ import annotations

import pytest


async def _login(client, email: str = "keys@example.com") -> str:
    await client.post(
        "/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery-staple"},
    )
    r = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": "correct-horse-battery-staple"},
    )
    return r.json()["access_token"]


@pytest.mark.asyncio
async def test_multiple_keys_same_slug_allowed(client) -> None:  # type: ignore[no-untyped-def]
    token = await _login(client)
    h = {"Authorization": f"Bearer {token}"}

    r1 = await client.post(
        "/v1/users/me/api-keys",
        json={"agent_name": "forge one", "agent_slug": "forge-agent"},
        headers=h,
    )
    assert r1.status_code == 201, r1.text
    r2 = await client.post(
        "/v1/users/me/api-keys",
        json={"agent_name": "forge two", "agent_slug": "forge-agent"},
        headers=h,
    )
    # Used to 409 on the unique constraint — now both keys share the scope.
    assert r2.status_code == 201, r2.text
    assert r1.json()["agent_slug"] == r2.json()["agent_slug"] == "forge-agent"
    assert r1.json()["key"] != r2.json()["key"]


@pytest.mark.asyncio
async def test_slug_is_sanitized_not_rejected(client) -> None:  # type: ignore[no-untyped-def]
    token = await _login(client, "sanitize@example.com")
    h = {"Authorization": f"Bearer {token}"}

    # Underscores / case / punctuation used to be a 422 "pattern" error.
    r = await client.post(
        "/v1/users/me/api-keys",
        json={"agent_name": "x", "agent_slug": "Forge_Agent.01"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    assert r.json()["agent_slug"] == "forge-agent-01"


@pytest.mark.asyncio
async def test_scope_edit_sanitizes(client) -> None:  # type: ignore[no-untyped-def]
    token = await _login(client, "edit@example.com")
    h = {"Authorization": f"Bearer {token}"}
    key_id = (await client.post(
        "/v1/users/me/api-keys",
        json={"agent_name": "agent"}, headers=h,
    )).json()["id"]

    # Edit to a messy value → normalized, not rejected.
    r = await client.patch(
        f"/v1/users/me/api-keys/{key_id}/scope",
        json={"agent_slug": "My Cool Agent!"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["agent_slug"] == "my-cool-agent"

    # Clear it.
    r = await client.patch(
        f"/v1/users/me/api-keys/{key_id}/scope",
        json={"agent_slug": None},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["agent_slug"] is None
