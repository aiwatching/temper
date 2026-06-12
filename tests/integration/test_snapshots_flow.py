"""Per-user memory snapshots: create / list / restore / delete.

Snapshots are blocks + documents by default; episodes are opt-in. We
exercise the blocks path (no Graphiti needed) end-to-end, then the
restore-after-change roundtrip.
"""
from __future__ import annotations

import pytest


async def _register_and_login(client, email: str = "snap@example.com") -> str:
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
async def test_snapshot_create_list_get(client) -> None:  # type: ignore[no-untyped-def]
    token = await _register_and_login(client)
    h = {"Authorization": f"Bearer {token}"}

    # Seed a block so the snapshot has content.
    await client.put(
        "/v1/memory/blocks/state.current_focus",
        json={"value": "shipping the snapshot feature"},
        headers=h,
    )

    r = await client.post("/v1/me/snapshots", json={"note": "v1"}, headers=h)
    assert r.status_code == 201, r.text
    meta = r.json()
    assert meta["kind"] == "manual"
    assert meta["blocks_count"] >= 1
    assert meta["episodes_count"] == 0          # episodes excluded by default
    assert meta["note"] == "v1"
    snap_id = meta["id"]

    r = await client.get("/v1/me/snapshots", headers=h)
    assert r.status_code == 200
    assert any(s["id"] == snap_id for s in r.json()["snapshots"])

    # Full bundle download.
    r = await client.get(f"/v1/me/snapshots/{snap_id}", headers=h)
    assert r.status_code == 200
    bundle = r.json()
    assert bundle["format_version"] == "1"
    assert any(b["key"] == "state.current_focus" for b in bundle["blocks"])


@pytest.mark.asyncio
async def test_snapshot_restore_brings_back_deleted_block(client) -> None:  # type: ignore[no-untyped-def]
    token = await _register_and_login(client, "restore@example.com")
    h = {"Authorization": f"Bearer {token}"}

    await client.put(
        "/v1/memory/blocks/pref.tone",
        json={"value": "concise"},
        headers=h,
    )
    snap_id = (await client.post("/v1/me/snapshots", json={}, headers=h)).json()["id"]

    # Mutate after the snapshot.
    await client.put(
        "/v1/memory/blocks/pref.tone",
        json={"value": "verbose"},
        headers=h,
    )

    # Restore (merge) → the snapshot value wins.
    r = await client.post(f"/v1/me/snapshots/{snap_id}/restore", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["blocks"]["errored"] == 0

    r = await client.get("/v1/memory/blocks/pref.tone", headers=h)
    assert r.json()["block_value"] == "concise"


@pytest.mark.asyncio
async def test_snapshot_isolation_and_404(client) -> None:  # type: ignore[no-untyped-def]
    token_a = await _register_and_login(client, "owner-a@example.com")
    token_b = await _register_and_login(client, "owner-b@example.com")
    ha = {"Authorization": f"Bearer {token_a}"}
    hb = {"Authorization": f"Bearer {token_b}"}

    snap_id = (await client.post("/v1/me/snapshots", json={}, headers=ha)).json()["id"]

    # B can't see or touch A's snapshot.
    assert (await client.get(f"/v1/me/snapshots/{snap_id}", headers=hb)).status_code == 404
    assert (await client.post(f"/v1/me/snapshots/{snap_id}/restore", headers=hb)).status_code == 404
    assert (await client.delete(f"/v1/me/snapshots/{snap_id}", headers=hb)).status_code == 404

    # A can delete their own.
    assert (await client.delete(f"/v1/me/snapshots/{snap_id}", headers=ha)).status_code == 204
    assert (await client.get(f"/v1/me/snapshots/{snap_id}", headers=ha)).status_code == 404


@pytest.mark.asyncio
async def test_snapshots_require_auth(client) -> None:  # type: ignore[no-untyped-def]
    assert (await client.get("/v1/me/snapshots")).status_code == 401
    assert (await client.post("/v1/me/snapshots", json={})).status_code == 401
