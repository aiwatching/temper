"""Smoke test for the typed memory endpoints.

Covers:
  - POST /v1/memory/tasks + GET /v1/memory/tasks (list)
  - PATCH /v1/memory/tasks/{id} (update)
  - POST /v1/memory/tasks/{id}/complete (atomic block + episode)
  - PUT /v1/memory/focus + GET /v1/memory/focus
  - PUT /v1/memory/preferences/{key} + GET /v1/memory/preferences
  - GET /v1/memory/turn_context (bundle, with query → recall)

Graphiti is stubbed (same pattern as test_episode_flow.py). The point
of these tests is to verify routing — that a write to typed endpoint X
lands in the right block / episode store — not to re-test graphiti.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


async def _register_and_login(client, email: str = "tm@example.com") -> str:
    await client.post(
        "/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery-staple"},
    )
    r = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": "correct-horse-battery-staple"},
    )
    return r.json()["access_token"]


def _fake_episode_result(uuid: str) -> SimpleNamespace:
    episode = SimpleNamespace(uuid=uuid, created_at=datetime.now(UTC), content="x")
    return SimpleNamespace(
        episode=episode,
        episodic_edges=[], nodes=[], edges=[],
        communities=[], community_edges=[],
    )


@pytest.fixture
def mock_graphiti():
    counter = {"n": 0}

    def _next(*_a, **_kw):
        counter["n"] += 1
        return _fake_episode_result(f"ep-{counter['n']}")

    empty = SimpleNamespace(edges=[], nodes=[], episodes=[], communities=[])
    # core/memory.search() takes the single-namespace fast path when
    # there's only one group_id and clones `client.driver` — sub in a
    # stub so the clone() call doesn't trip on `None`.
    fake_driver = SimpleNamespace(clone=lambda database: SimpleNamespace())
    fake = SimpleNamespace(
        add_episode=AsyncMock(side_effect=_next),
        search_=AsyncMock(return_value=empty),
        driver=fake_driver,
    )
    with patch("memory_service.core.memory.get_graphiti", return_value=fake):
        yield fake


@pytest.mark.asyncio
async def test_task_lifecycle(client, mock_graphiti) -> None:  # type: ignore[no-untyped-def]
    token = await _register_and_login(client)
    h = {"Authorization": f"Bearer {token}"}

    # Add two tasks
    r1 = await client.post("/v1/memory/tasks", json={"title": "Smith P6 resource layer"}, headers=h)
    assert r1.status_code == 201, r1.text
    t1 = r1.json()
    assert t1["status"] == "todo"
    assert t1["priority"] == 50

    r2 = await client.post(
        "/v1/memory/tasks",
        json={"title": "memctl tasks subcommand", "priority": 80, "status": "doing"},
        headers=h,
    )
    assert r2.status_code == 201
    t2 = r2.json()

    # List → 2 items, sorted by priority desc
    rl = await client.get("/v1/memory/tasks", headers=h)
    assert rl.status_code == 200
    tasks = rl.json()["tasks"]
    assert len(tasks) == 2
    assert tasks[0]["title"] == "memctl tasks subcommand"  # priority 80 first

    # Filter by status
    rd = await client.get("/v1/memory/tasks?status=doing", headers=h)
    assert [t["id"] for t in rd.json()["tasks"]] == [t2["id"]]

    # Update
    rp = await client.patch(
        f"/v1/memory/tasks/{t1['id']}",
        json={"status": "blocked", "notes": "waiting on upstream"},
        headers=h,
    )
    assert rp.status_code == 200
    assert rp.json()["status"] == "blocked"
    assert rp.json()["notes"] == "waiting on upstream"

    # Complete t1 — block update + graphiti episode (atomic)
    rc = await client.post(
        f"/v1/memory/tasks/{t1['id']}/complete",
        json={"summary": "obsolete, dropping"},
        headers=h,
    )
    assert rc.status_code == 200, rc.text
    body = rc.json()
    assert body["completed"]["status"] == "done"
    assert body["episode_id"]
    # Graphiti got exactly one add_episode call for the completion log
    assert mock_graphiti.add_episode.await_count == 1

    # Active list now only has t2
    rl2 = await client.get("/v1/memory/tasks", headers=h)
    remaining = rl2.json()["tasks"]
    assert len(remaining) == 1
    assert remaining[0]["id"] == t2["id"]


@pytest.mark.asyncio
async def test_focus_set_get(client, mock_graphiti) -> None:  # type: ignore[no-untyped-def]
    token = await _register_and_login(client, "focus@example.com")
    h = {"Authorization": f"Bearer {token}"}

    # No focus → null
    r0 = await client.get("/v1/memory/focus", headers=h)
    assert r0.status_code == 200
    assert r0.json()["value"] is None

    # Set focus → episode written
    r1 = await client.put(
        "/v1/memory/focus",
        json={"value": "fortinet-auth-rewrite", "note": "kicked off this morning"},
        headers=h,
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["value"] == "fortinet-auth-rewrite"
    assert r1.json()["episode_id"]
    assert mock_graphiti.add_episode.await_count == 1

    # Same value again → no new episode (no-op set)
    r2 = await client.put(
        "/v1/memory/focus",
        json={"value": "fortinet-auth-rewrite"},
        headers=h,
    )
    assert r2.status_code == 200
    assert mock_graphiti.add_episode.await_count == 1  # unchanged

    # GET
    rg = await client.get("/v1/memory/focus", headers=h)
    assert rg.json()["value"] == "fortinet-auth-rewrite"


@pytest.mark.asyncio
async def test_preferences_list_and_set(client, mock_graphiti) -> None:  # type: ignore[no-untyped-def]
    token = await _register_and_login(client, "prefs@example.com")
    h = {"Authorization": f"Bearer {token}"}

    # Set two preferences (no graphiti involved — pure block writes)
    r1 = await client.put(
        "/v1/memory/preferences/language",
        json={"value": "Chinese", "description": "default reply language"},
        headers=h,
    )
    assert r1.status_code == 200, r1.text

    r2 = await client.put(
        "/v1/memory/preferences/communication_style",
        json={"value": "terse, no preamble"},
        headers=h,
    )
    assert r2.status_code == 200

    rl = await client.get("/v1/memory/preferences", headers=h)
    assert rl.status_code == 200
    keys = {p["key"] for p in rl.json()["preferences"]}
    assert keys == {"language", "communication_style"}
    # The bare key — verify we strip the "preferences." prefix on the way out
    assert all(not p["key"].startswith("preferences.") for p in rl.json()["preferences"])


@pytest.mark.asyncio
async def test_preferences_reject_already_prefixed_key(  # type: ignore[no-untyped-def]
    client, mock_graphiti,
) -> None:
    token = await _register_and_login(client, "prefix@example.com")
    h = {"Authorization": f"Bearer {token}"}
    r = await client.put(
        "/v1/memory/preferences/preferences.language",
        json={"value": "x"},
        headers=h,
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_turn_context_bundles_pinned_plus_recall(  # type: ignore[no-untyped-def]
    client, mock_graphiti,
) -> None:
    token = await _register_and_login(client, "ctx@example.com")
    h = {"Authorization": f"Bearer {token}"}

    # Seed: 1 task, 1 focus, 1 preference
    await client.post("/v1/memory/tasks", json={"title": "Smith P6"}, headers=h)
    await client.put("/v1/memory/focus", json={"value": "smith"}, headers=h)
    await client.put(
        "/v1/memory/preferences/language", json={"value": "Chinese"}, headers=h,
    )

    # No query → no recall fired
    r0 = await client.get("/v1/memory/turn_context", headers=h)
    assert r0.status_code == 200
    body = r0.json()
    assert len(body["active_tasks"]) == 1
    assert body["current_focus"] == "smith"
    assert body["preferences"] == {"language": "Chinese"}
    assert body["recalled_episodes"] == []
    assert body["namespaces_searched"] == []
    assert mock_graphiti.search_.await_count == 0

    # Pinned bundle includes the three canonical blocks
    keys = {p["key"] for p in body["pinned_blocks"]}
    assert "state.active_tasks" in keys
    assert "state.current_focus" in keys
    assert "preferences.language" in keys

    # With query → recall fires (graphiti returns empty in the fake)
    r1 = await client.get("/v1/memory/turn_context?query=hello", headers=h)
    assert r1.status_code == 200
    assert mock_graphiti.search_.await_count >= 1
    assert r1.json()["namespaces_searched"]  # populated


@pytest.mark.asyncio
async def test_note_event_writes_episode(client, mock_graphiti) -> None:  # type: ignore[no-untyped-def]
    token = await _register_and_login(client, "ev@example.com")
    h = {"Authorization": f"Bearer {token}"}
    r = await client.post(
        "/v1/memory/events",
        json={"content": "Met Bob about the auth project"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["episode_id"]
    assert body["namespace"].startswith("user:")
    assert mock_graphiti.add_episode.await_count == 1
