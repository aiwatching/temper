"""Temper client. Async wrapper over the Memory Service HTTP API.

Keeps Smith decoupled from TEMPER internals: we only call documented
endpoints from /v1/. If the agent wants a primitive that doesn't exist,
the right move is to add the endpoint in TEMPER and re-call from here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from smith.config import get_settings


class TemperError(RuntimeError):
    """Raised when the Memory Service returns a non-2xx response.

    Carries the HTTP status so callers can branch on 401 / 404 / 5xx
    without re-parsing the message.
    """
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"Temper {status}: {detail}")
        self.status = status
        self.detail = detail


class Temper:
    """Thin async client. Reuses one httpx.AsyncClient for connection
    pooling across many calls in a long-running agent.

    Use as an async context manager so close() lands deterministically:

        async with Temper() as t:
            await t.write("Alice prefers Postgres", source_description="chat")
            hits = await t.search("Alice database preference")
    """

    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        s = get_settings()
        self.base_url = (base_url or s.temper_base_url).rstrip("/")
        self.api_key = api_key or s.temper_api_key
        if not self.api_key:
            raise TemperError(
                0, "TEMPER_API_KEY is not set — create one at /admin/integrate"
            )
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"X-API-Key": self.api_key, "Content-Type": "application/json"},
            timeout=30.0,
        )

    async def __aenter__(self) -> "Temper":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    async def _req(self, method: str, path: str, **kw: Any) -> Any:
        r = await self._client.request(method, path, **kw)
        if r.status_code >= 400:
            detail = ""
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text
            raise TemperError(r.status_code, str(detail))
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    # ---- memory ----

    async def write(
        self,
        content: str,
        *,
        source_type: str = "message",
        source_description: str | None = None,
        reference_time: datetime | str | None = None,
        tags: list[str] | None = None,
        saga: str | None = None,
        namespace: str | None = None,
        async_extract: bool = False,
    ) -> dict[str, Any]:
        """POST /v1/episodes. Returns the episode payload Temper echoes back."""
        body: dict[str, Any] = {"content": content, "source_type": source_type}
        if source_description is not None:
            body["source_description"] = source_description
        if reference_time is not None:
            body["reference_time"] = (
                reference_time.isoformat()
                if isinstance(reference_time, datetime)
                else reference_time
            )
        if tags:
            body["tags"] = tags
        if saga:
            body["saga"] = saga
        if namespace:
            body["namespace"] = namespace
        params = {"async_extract": "true"} if async_extract else None
        return await self._req("POST", "/v1/episodes", json=body, params=params)

    async def search(
        self,
        query: str,
        *,
        limit: int = 10,
        namespaces: list[str] | None = None,
        node_labels: list[str] | None = None,
        edge_types: list[str] | None = None,
        as_of: datetime | str | None = None,
        center: str | None = None,
    ) -> list[dict[str, Any]]:
        """GET /v1/search. Returns the `hits` list directly (one less level
        of nesting for callers)."""
        params: dict[str, Any] = {"query": query, "limit": limit}
        if namespaces:
            params["namespaces"] = ",".join(namespaces)
        if node_labels:
            params["node_labels"] = ",".join(node_labels)
        if edge_types:
            params["edge_types"] = ",".join(edge_types)
        if as_of is not None:
            params["as_of"] = (
                as_of.isoformat() if isinstance(as_of, datetime) else as_of
            )
        if center:
            params["center"] = center
        body = await self._req("GET", "/v1/search", params=params)
        return body.get("hits", [])

    async def health(self) -> dict[str, Any]:
        return await self._req("GET", "/v1/health")

    async def whoami(self) -> dict[str, Any]:
        return await self._req("GET", "/v1/auth/me")
