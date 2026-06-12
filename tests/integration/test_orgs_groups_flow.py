"""Orgs + groups CRUD + episode permission matrix for those namespaces.

Covers the Phase 1.3/1.4 behaviour: who can create orgs/groups, who can
add/remove members, who can write to `org:<slug>` / `group:<slug>` once
the model is wired up.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


# ---- helpers ----------------------------------------------------------


async def _register(client, email: str, password: str = "correct-horse-battery-staple") -> str:
    """Register a fresh user. Returns their access token."""
    await client.post(
        "/v1/auth/register",
        json={"email": email, "password": password},
    )
    r = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": password},
    )
    return r.json()["access_token"]


async def _me(client, token: str) -> dict:
    r = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    return r.json()


async def _promote_super_admin(client, email: str) -> None:
    """Backdoor: flip is_super_admin in the DB. The API doesn't expose this
    on purpose — super_admin bootstraps from BOOTSTRAP_SUPER_ADMIN_EMAIL.
    """
    from sqlalchemy import update

    from memory_service.db.session import init_database
    from memory_service.config import get_settings
    from memory_service.models import User

    db = init_database(get_settings())
    async for s in db.session():
        await s.execute(update(User).where(User.email == email).values(is_super_admin=True))
        await s.commit()
        break


@pytest.fixture
def mock_graphiti():
    """Stub Graphiti so episode writes touch only the permission layer."""
    fake_episode = SimpleNamespace(uuid="ep-uuid-1", created_at=__import__("datetime").datetime.now(__import__("datetime").UTC))
    fake_result = SimpleNamespace(
        episode=fake_episode,
        nodes=[],
        edges=[],
        episodic_edges=[],
        communities=[],
        community_edges=[],
    )
    fake_client = SimpleNamespace(
        add_episode=AsyncMock(return_value=fake_result),
        search_=AsyncMock(return_value=SimpleNamespace(edges=[], nodes=[], episodes=[], communities=[])),
        driver=None,
    )
    with patch("memory_service.core.memory.get_graphiti", return_value=fake_client):
        yield fake_client


# ---- orgs -------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_super_admin_can_create_orgs(client) -> None:  # type: ignore[no-untyped-def]
    plain_token = await _register(client, "plain@example.com")
    r = await client.post(
        "/v1/orgs",
        json={"slug": "acme", "name": "Acme Corp"},
        headers={"Authorization": f"Bearer {plain_token}"},
    )
    assert r.status_code == 403, r.text

    await _promote_super_admin(client, "plain@example.com")
    r = await client.post(
        "/v1/orgs",
        json={"slug": "acme", "name": "Acme Corp"},
        headers={"Authorization": f"Bearer {plain_token}"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["slug"] == "acme"
    assert body["member_count"] == 0


@pytest.mark.asyncio
async def test_org_member_lifecycle(client) -> None:  # type: ignore[no-untyped-def]
    admin_token = await _register(client, "admin@example.com")
    await _promote_super_admin(client, "admin@example.com")
    alice_token = await _register(client, "alice@example.com")
    alice_id = (await _me(client, alice_token))["id"]

    await client.post(
        "/v1/orgs",
        json={"slug": "acme", "name": "Acme"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # super_admin adds Alice to the org (membership is flat — no per-org
    # admin role; org management is super_admin-only).
    r = await client.post(
        "/v1/orgs/acme/members",
        json={"user_id": alice_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["email"] == "alice@example.com"

    # Alice can now see members of her org
    r = await client.get(
        "/v1/orgs/acme/members",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert r.status_code == 200
    members = r.json()
    assert len(members) == 1
    assert members[0]["email"] == "alice@example.com"

    # A user in no org can't view acme members
    bob_token = await _register(client, "bob@example.com")
    r = await client.get(
        "/v1/orgs/acme/members",
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert r.status_code == 403, r.text

    # A plain member can't add people (super_admin-only)
    r = await client.post(
        "/v1/orgs/acme/members",
        json={"user_id": "11111111-1111-1111-1111-111111111111"},
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert r.status_code == 403

    # But a member can remove themselves (self-leave).
    r = await client.delete(
        f"/v1/orgs/acme/members/{alice_id}",
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert r.status_code == 204, r.text


@pytest.mark.asyncio
async def test_org_write_requires_super_admin(client, mock_graphiti) -> None:  # type: ignore[no-untyped-def]
    admin_token = await _register(client, "root@example.com")
    await _promote_super_admin(client, "root@example.com")
    alice_token = await _register(client, "alice@example.com")
    alice_id = (await _me(client, alice_token))["id"]

    await client.post(
        "/v1/orgs",
        json={"slug": "acme", "name": "Acme"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await client.post(
        "/v1/orgs/acme/members",
        json={"user_id": alice_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Plain org member can't write to the org namespace — that's
    # super_admin-only by design.
    r = await client.post(
        "/v1/episodes",
        json={"namespace": "org:acme", "content": "a shared organization fact worth recording"},
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert r.status_code == 403
    assert "super_admin" in r.json()["detail"]

    # super_admin can write to the org namespace.
    r = await client.post(
        "/v1/episodes",
        json={"namespace": "org:acme", "content": "a shared organization fact worth recording"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 201, r.text


# ---- groups -----------------------------------------------------------


@pytest.mark.asyncio
async def test_super_admin_creates_group_and_adds_members(client) -> None:  # type: ignore[no-untyped-def]
    admin_token = await _register(client, "root@example.com")
    await _promote_super_admin(client, "root@example.com")
    alice_token = await _register(client, "alice@example.com")
    alice_id = (await _me(client, alice_token))["id"]
    bob_token = await _register(client, "bob@example.com")
    bob_id = (await _me(client, bob_token))["id"]

    # Set up org with Alice + Bob
    await client.post(
        "/v1/orgs",
        json={"slug": "acme", "name": "Acme"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    for uid in (alice_id, bob_id):
        await client.post(
            "/v1/orgs/acme/members",
            json={"user_id": uid},
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    # A plain member can't create a group — super_admin only.
    r = await client.post(
        "/v1/groups",
        json={"slug": "engineers", "name": "Engineering", "org_slug": "acme"},
        headers={"Authorization": f"Bearer {alice_token}"},
    )
    assert r.status_code == 403, r.text

    # super_admin creates it (must name the owning org).
    r = await client.post(
        "/v1/groups",
        json={"slug": "engineers", "name": "Engineering", "org_slug": "acme"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["org_slug"] == "acme"

    # Bob (org member, not in group) sees the group exists but can't
    # read its members.
    r = await client.get(
        "/v1/groups",
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert {g["slug"] for g in r.json()} == {"engineers"}
    r = await client.get(
        "/v1/groups/engineers/members",
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert r.status_code == 403

    # Bob can't add himself — super_admin only.
    r = await client.post(
        "/v1/groups/engineers/members",
        json={"user_id": bob_id},
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert r.status_code == 403

    # super_admin adds Bob → Bob now sees members.
    r = await client.post(
        "/v1/groups/engineers/members",
        json={"user_id": bob_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 201
    r = await client.get(
        "/v1/groups/engineers/members",
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert r.status_code == 200
    assert {m["email"] for m in r.json()} == {"bob@example.com"}


@pytest.mark.asyncio
async def test_group_write_requires_membership(client, mock_graphiti) -> None:  # type: ignore[no-untyped-def]
    admin_token = await _register(client, "root@example.com")
    await _promote_super_admin(client, "root@example.com")
    bob_token = await _register(client, "bob@example.com")
    bob_id = (await _me(client, bob_token))["id"]

    await client.post(
        "/v1/orgs",
        json={"slug": "acme", "name": "Acme"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await client.post(
        "/v1/orgs/acme/members",
        json={"user_id": bob_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await client.post(
        "/v1/groups",
        json={"slug": "engineers", "name": "Engineering", "org_slug": "acme"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Bob (org member, not in group) can't write to group:engineers
    r = await client.post(
        "/v1/episodes",
        json={"namespace": "group:engineers", "content": "a durable engineering team fact to remember"},
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert r.status_code == 403
    assert "engineers" in r.json()["detail"]

    # Add Bob; now he can write
    await client.post(
        "/v1/groups/engineers/members",
        json={"user_id": bob_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    r = await client.post(
        "/v1/episodes",
        json={"namespace": "group:engineers", "content": "a durable engineering team fact to remember"},
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_non_super_admin_cannot_create_group(client) -> None:  # type: ignore[no-untyped-def]
    token = await _register(client, "solo@example.com")
    r = await client.post(
        "/v1/groups",
        json={"slug": "myteam", "name": "Solo Team", "org_slug": "acme"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403
    assert "super_admin" in r.json()["detail"]


@pytest.mark.asyncio
async def test_create_group_requires_org_slug(client) -> None:  # type: ignore[no-untyped-def]
    admin_token = await _register(client, "root@example.com")
    await _promote_super_admin(client, "root@example.com")
    r = await client.post(
        "/v1/groups",
        json={"slug": "myteam", "name": "Solo Team"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert r.status_code == 400
    assert "org" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_self_leave_group(client) -> None:  # type: ignore[no-untyped-def]
    admin_token = await _register(client, "root@example.com")
    await _promote_super_admin(client, "root@example.com")
    bob_token = await _register(client, "bob@example.com")
    bob_id = (await _me(client, bob_token))["id"]

    await client.post(
        "/v1/orgs",
        json={"slug": "acme", "name": "Acme"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await client.post(
        "/v1/orgs/acme/members",
        json={"user_id": bob_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await client.post(
        "/v1/groups",
        json={"slug": "engineers", "name": "Engineering", "org_slug": "acme"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    await client.post(
        "/v1/groups/engineers/members",
        json={"user_id": bob_id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Bob (plain member) can remove himself even though he's not super_admin.
    r = await client.delete(
        f"/v1/groups/engineers/members/{bob_id}",
        headers={"Authorization": f"Bearer {bob_token}"},
    )
    assert r.status_code == 204, r.text
