"""Documents with `[[wikilink]]`: in-namespace links must not 500.

Regression for the 0017 fix. Pre-0017 the document_links PK
(source_document_id, target_path, target_namespace) accepted NULL on
target_namespace, but Postgres rejects NULL in PK columns — so any
in-namespace wikilink killed the document PUT with HTTP 500. The fix
swaps the NULL sentinel for ""; this test exercises the upsert path
on a real save+update, plus the cross-namespace shape that should
still work.
"""
from __future__ import annotations

import pytest


async def _login(client, email: str = "wl@example.com") -> str:
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
async def test_put_document_with_in_namespace_wikilink(client) -> None:  # type: ignore[no-untyped-def]
    """In-namespace wikilink + update path — the exact shape that 500'd."""
    token = await _login(client)
    h = {"Authorization": f"Bearer {token}"}
    body = {
        "title": "Migration archive",
        "content": "see [[chats/other-chat]] and also [[notes/setup]] for context",
    }
    # First write (create)
    r = await client.put("/v1/documents/chats/main.md", json=body, headers=h)
    assert r.status_code == 200, r.text
    # Second write (update — the original failure surfaced here too)
    body["content"] += "\nUpdated with [[notes/setup]] reference."
    r = await client.put("/v1/documents/chats/main.md", json=body, headers=h)
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_cross_namespace_wikilink_still_works(client) -> None:  # type: ignore[no-untyped-def]
    """Explicit cross-namespace `[[group:foo/path]]` must still parse + persist."""
    token = await _login(client, "wl2@example.com")
    h = {"Authorization": f"Bearer {token}"}
    body = {
        "title": "Cross-ns",
        "content": "ref [[group:engineers/runbook]] and [[user:me/notes]]",
    }
    r = await client.put("/v1/documents/runbooks/index.md", json=body, headers=h)
    assert r.status_code == 200, r.text
