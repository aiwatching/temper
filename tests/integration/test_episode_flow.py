"""Episode write / list / get / search flow.

These tests stub Graphiti so the assertions cover our adapter layer +
permission checks without paying for real LLM calls. One slow path
hitting the real backend lives in `scripts/test/`.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


# ---- helpers -------------------------------------------------------------


async def _register_and_login(client, email: str = "ep@example.com") -> str:
    await client.post(
        "/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery-staple"},
    )
    r = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": "correct-horse-battery-staple"},
    )
    return r.json()["access_token"]


def _fake_add_episode_result(episode_uuid: str = "ep-uuid-1") -> SimpleNamespace:
    """Mimic graphiti_core.graphiti.AddEpisodeResults enough for our wrapper."""
    episode = SimpleNamespace(
        uuid=episode_uuid,
        created_at=datetime.now(UTC),
        content="Jerry's English teacher is Sarah.",
    )
    nodes = [
        SimpleNamespace(uuid="n1", name="Jerry", labels=["Person"], summary="A user"),
        SimpleNamespace(uuid="n2", name="Sarah", labels=["Person"], summary="A teacher"),
    ]
    edges = [
        SimpleNamespace(
            uuid="f1",
            fact="Jerry has English teacher Sarah",
            source_node_uuid="n1",
            target_node_uuid="n2",
            valid_at=datetime.now(UTC),
            invalid_at=None,
            episodes=[episode_uuid],
            group_id="user:dummy",
        )
    ]
    return SimpleNamespace(
        episode=episode,
        episodic_edges=[],
        nodes=nodes,
        edges=edges,
        communities=[],
        community_edges=[],
    )


# ---- fixtures ------------------------------------------------------------


@pytest.fixture
def mock_graphiti():
    """Patch get_graphiti() to return a fake client with the methods we hit."""
    empty_results = SimpleNamespace(edges=[], nodes=[], episodes=[], communities=[])
    fake_client = SimpleNamespace(
        add_episode=AsyncMock(return_value=_fake_add_episode_result()),
        search_=AsyncMock(return_value=empty_results),
        driver=None,
    )
    with patch("memory_service.core.memory.get_graphiti", return_value=fake_client):
        yield fake_client


# ---- tests ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_episode_with_default_namespace(client, mock_graphiti) -> None:  # type: ignore[no-untyped-def]
    token = await _register_and_login(client)
    r = await client.post(
        "/v1/episodes",
        json={"content": "Jerry's English teacher is Sarah."},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["episode_id"] == "ep-uuid-1"
    assert body["namespace"].startswith("user:")
    assert len(body["extracted_entities"]) == 2
    assert {e["name"] for e in body["extracted_entities"]} == {"Jerry", "Sarah"}
    assert mock_graphiti.add_episode.await_count == 1


@pytest.mark.asyncio
async def test_create_episode_into_other_users_namespace_forbidden(  # type: ignore[no-untyped-def]
    client, mock_graphiti
) -> None:
    token = await _register_and_login(client, "alice@example.com")
    r = await client.post(
        "/v1/episodes",
        json={
            "namespace": "user:00000000-0000-0000-0000-000000000000",
            "content": "trying to write into someone else's space",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403, r.text
    assert mock_graphiti.add_episode.await_count == 0


@pytest.mark.asyncio
async def test_list_episodes_after_write(client, mock_graphiti) -> None:  # type: ignore[no-untyped-def]
    token = await _register_and_login(client)
    headers = {"Authorization": f"Bearer {token}"}
    await client.post("/v1/episodes", json={"content": "fact 1"}, headers=headers)
    # Different episode UUID so unique-PK doesn't trip
    mock_graphiti.add_episode.return_value = _fake_add_episode_result("ep-uuid-2")
    await client.post("/v1/episodes", json={"content": "fact 2"}, headers=headers)

    r = await client.get("/v1/episodes?limit=20", headers=headers)
    assert r.status_code == 200
    items = r.json()["episodes"]
    assert len(items) == 2
    ids = {it["episode_id"] for it in items}
    assert ids == {"ep-uuid-1", "ep-uuid-2"}


@pytest.mark.asyncio
async def test_delete_episode_owner_only(client, mock_graphiti) -> None:  # type: ignore[no-untyped-def]
    token_a = await _register_and_login(client, "ownera@example.com")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    await client.post("/v1/episodes", json={"content": "owner A's fact"}, headers=headers_a)

    # User B tries to delete A's episode -> 404 (we don't leak existence)
    token_b = await _register_and_login(client, "ownerb@example.com")
    r = await client.delete(
        "/v1/episodes/ep-uuid-1",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_search_with_no_query_returns_empty(client, mock_graphiti) -> None:  # type: ignore[no-untyped-def]
    token = await _register_and_login(client)
    r = await client.get(
        "/v1/search?query= ",
        headers={"Authorization": f"Bearer {token}"},
    )
    # min_length=1 on Query, so " " is acceptable but search() returns []
    # because the trimmed query is empty.
    assert r.status_code == 200
    assert r.json()["facts"] == []
    assert mock_graphiti.search_.await_count == 0  # short-circuited


@pytest.mark.asyncio
async def test_search_calls_graphiti(client, mock_graphiti) -> None:  # type: ignore[no-untyped-def]
    token = await _register_and_login(client)

    # Capture the group_ids the service hands to Graphiti so we can echo one
    # back in the fake response — this is what makes the encoded->raw mapping
    # in core.memory.search testable end-to-end.
    captured: dict[str, list[str]] = {}

    async def fake_search_(query, config, group_ids=None, **kw):  # type: ignore[no-untyped-def]
        captured["group_ids"] = list(group_ids or [])
        encoded = group_ids[0]
        return SimpleNamespace(
            edges=[
                SimpleNamespace(
                    uuid="f1",
                    fact="Jerry has English teacher Sarah",
                    group_id=encoded,
                    episodes=["ep-uuid-1"],
                    valid_at=datetime.now(UTC),
                    invalid_at=None,
                )
            ],
            nodes=[
                SimpleNamespace(
                    uuid="n1",
                    name="Sarah",
                    summary="Sarah is Jerry's English teacher.",
                    group_id=encoded,
                )
            ],
            episodes=[],
            communities=[],
        )

    mock_graphiti.search_.side_effect = fake_search_

    r = await client.get(
        "/v1/search?query=who is the teacher",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    facts = body["facts"]
    assert len(facts) == 2
    assert {h["kind"] for h in facts} == {"fact", "entity"}
    edge_hit = next(h for h in facts if h["kind"] == "fact")
    assert edge_hit["fact"] == "Jerry has English teacher Sarah"
    entity_hit = next(h for h in facts if h["kind"] == "entity")
    assert entity_hit["fact"] == "Sarah is Jerry's English teacher."
    # Both hits should surface the raw API-form namespace, not the encoded
    # Graphiti group_id we received from search_().
    assert edge_hit["namespace"].startswith("user:")
    assert "__" not in edge_hit["namespace"]
    assert entity_hit["namespace"] == edge_hit["namespace"]
    # And we did send the encoded form to Graphiti.
    assert any("__" in g for g in captured["group_ids"])
    assert mock_graphiti.search_.await_count == 1


@pytest.mark.asyncio
async def test_episodes_require_auth(client) -> None:  # type: ignore[no-untyped-def]
    r = await client.post("/v1/episodes", json={"content": "x"})
    assert r.status_code == 401
    r = await client.get("/v1/episodes")
    assert r.status_code == 401
    r = await client.get("/v1/search?query=x")
    assert r.status_code == 401
    r = await client.get("/v1/graph")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_graph_endpoint_shapes_response(client, mock_graphiti) -> None:  # type: ignore[no-untyped-def]
    """End-to-end smoke for /v1/graph: stub the driver to return one
    Episodic + one Entity + one RELATES_TO edge, assert the API translates
    that into the documented JSON shape."""
    token = await _register_and_login(client)

    class _FakeRecord(dict):
        pass

    fake_nodes = [
        _FakeRecord(
            uuid="ep-1",
            kind="Episodic",
            name=None,
            summary=None,
            content="Sarah lives in Toronto.",
        ),
        _FakeRecord(uuid="e-1", kind="Entity", name="Sarah", summary="Lives in Toronto", content=None),
        _FakeRecord(uuid="e-2", kind="Entity", name="Toronto", summary="City", content=None),
    ]
    fake_edges = [
        _FakeRecord(source="e-1", target="e-2", type="RELATES_TO", name="LIVES_IN", fact="Sarah lives in Toronto"),
        _FakeRecord(source="ep-1", target="e-1", type="MENTIONS", name=None, fact=None),
    ]

    class _Driver:
        async def execute_query(self, q, **kw):  # type: ignore[no-untyped-def]
            return (fake_nodes if "MATCH (n)" in q else fake_edges, None, None)

        def clone(self, database):  # type: ignore[no-untyped-def]
            return self

    mock_graphiti.driver = _Driver()
    r = await client.get(
        "/v1/graph?namespace=user:me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["namespace"].startswith("user:")
    assert "__" not in body["namespace"]
    assert len(body["nodes"]) == 3
    assert {n["kind"] for n in body["nodes"]} == {"Episodic", "Entity"}
    assert len(body["edges"]) == 2
    fact = next(e for e in body["edges"] if e["type"] == "RELATES_TO")
    assert fact["fact"] == "Sarah lives in Toronto"
    assert fact["name"] == "LIVES_IN"


@pytest.mark.asyncio
async def test_graph_denies_unreadable_namespace(client, mock_graphiti) -> None:  # type: ignore[no-untyped-def]
    token = await _register_and_login(client, "rando@example.com")
    r = await client.get(
        "/v1/graph?namespace=user:00000000-0000-0000-0000-000000000000",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403
