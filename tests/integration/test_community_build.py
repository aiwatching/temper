"""Community build runs in the background with a single-flight lock.

The real Graphiti clustering needs FalkorDB, so we patch the inner
build to a no-op and assert the API contract: 202 + status polling,
and 409 when a build is already running for the namespace.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


async def _login(client, email: str = "cb@example.com") -> str:
    await client.post(
        "/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery-staple"},
    )
    r = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": "correct-horse-battery-staple"},
    )
    return r.json()["access_token"]


@pytest.fixture
def graphiti_up():
    """Make _require_client() pass + the inner build a fast no-op."""
    from memory_service.core import memory

    with patch.object(memory, "get_graphiti", return_value=SimpleNamespace()), \
         patch.object(
             memory, "_do_build_communities",
             AsyncMock(return_value={
                 "namespace": "x",
                 "communities_created": 3,
                 "community_edges_created": 5,
             }),
         ):
        memory._community_jobs.clear()
        yield
        memory._community_jobs.clear()


@pytest.mark.asyncio
async def test_build_returns_202_then_done(client, graphiti_up) -> None:  # type: ignore[no-untyped-def]
    token = await _login(client)
    h = {"Authorization": f"Bearer {token}"}

    r = await client.post("/v1/admin/communities/build", headers=h)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "running"
    ns = body["namespace"]
    assert ns.startswith("user:")

    # The background task is near-instant (mocked); poll until done.
    for _ in range(20):
        s = await client.get(
            f"/v1/admin/communities/build/status?namespace={ns}", headers=h
        )
        if s.json()["status"] == "done":
            break
        import asyncio
        await asyncio.sleep(0.05)
    s = await client.get(
        f"/v1/admin/communities/build/status?namespace={ns}", headers=h
    )
    assert s.json()["status"] == "done"
    assert s.json()["communities_created"] == 3
    assert s.json()["community_edges_created"] == 5


@pytest.mark.asyncio
async def test_second_build_while_running_409(client, graphiti_up) -> None:  # type: ignore[no-untyped-def]
    from memory_service.core import memory

    token = await _login(client, "cb2@example.com")
    h = {"Authorization": f"Bearer {token}"}
    me = (await client.get("/v1/auth/me", headers=h)).json()
    ns = f"user:{me['id']}"

    # Simulate an in-flight build for this namespace.
    memory._community_jobs[ns] = {
        "status": "running",
        "started_at": "2026-06-12T00:00:00Z",
        "finished_at": None,
        "result": None,
        "error": None,
        "deadline": time.monotonic() + 600,
    }

    r = await client.post(
        f"/v1/admin/communities/build?namespace={ns}", headers=h
    )
    assert r.status_code == 409, r.text
    assert "already running" in r.json()["detail"]


@pytest.mark.asyncio
async def test_status_idle_when_never_run(client, graphiti_up) -> None:  # type: ignore[no-untyped-def]
    token = await _login(client, "cb3@example.com")
    h = {"Authorization": f"Bearer {token}"}
    s = await client.get("/v1/admin/communities/build/status", headers=h)
    assert s.status_code == 200
    assert s.json()["status"] == "idle"
