"""Smoke test: /v1/health responds and reports check details."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health_returns_payload(client) -> None:  # type: ignore[no-untyped-def]
    response = await client.get("/v1/health")
    assert response.status_code == 200
    body = response.json()

    assert body["version"] == "0.1.0"
    assert body["status"] in {"ok", "degraded"}
    assert set(body["checks"].keys()) == {"postgres", "falkordb", "graphiti"}
    for check in body["checks"].values():
        assert "ok" in check


@pytest.mark.asyncio
async def test_root_redirects_to_admin(client) -> None:  # type: ignore[no-untyped-def]
    response = await client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/admin"


@pytest.mark.asyncio
async def test_admin_page_loads(client) -> None:  # type: ignore[no-untyped-def]
    response = await client.get("/admin")
    assert response.status_code == 200
    assert "Memory Service" in response.text
