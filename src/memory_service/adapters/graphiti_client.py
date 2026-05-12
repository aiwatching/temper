"""Graphiti client wrapper.

Phase 1.1 keeps this thin — we just configure the client lazily and expose
a singleton. Subsequent phases will add `add_episode` / `search` proxies.

Graphiti requires an OpenAI key for entity extraction + embeddings. If the
key is absent (e.g. during smoke testing of the skeleton) we skip actual
initialization and surface that in the health endpoint instead of crashing.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from memory_service.config import Settings, get_settings

_logger = logging.getLogger(__name__)


@dataclass
class GraphitiStatus:
    initialized: bool
    detail: str


_client: object | None = None  # graphiti_core.Graphiti, kept untyped to defer import


def _build_client(settings: Settings) -> object | None:
    """Construct a Graphiti instance, or return None with a reason logged.

    Why this is tolerant: the v0.1 skeleton needs to come up even without
    an OPENAI_API_KEY so that local dev / curl /v1/health works before
    secrets are provisioned. The real adapter calls live in core/memory.py.
    """
    if not settings.openai_api_key:
        _logger.warning("OPENAI_API_KEY missing — Graphiti client not initialized")
        return None
    try:
        from graphiti_core import Graphiti  # type: ignore[import-untyped]
        from graphiti_core.driver.falkordb_driver import FalkorDriver  # type: ignore[import-untyped]
    except Exception as exc:  # ImportError or other init failure
        _logger.error("graphiti_core import failed: %s", exc)
        return None

    try:
        driver = FalkorDriver(
            host=settings.falkordb_host,
            port=settings.falkordb_port,
            password=settings.falkordb_password,
            database=settings.falkordb_graph_name,
        )
        client = Graphiti(graph_driver=driver)
        return client
    except Exception as exc:
        _logger.error("Graphiti init failed: %s", exc)
        return None


def get_graphiti(settings: Settings | None = None) -> object | None:
    """Lazily build the singleton. Returns None when init failed (key etc)."""
    global _client
    if _client is not None:
        return _client
    _client = _build_client(settings or get_settings())
    return _client


def graphiti_status(settings: Settings | None = None) -> GraphitiStatus:
    """Reports whether Graphiti was set up. Does not raise."""
    client = get_graphiti(settings)
    if client is None:
        settings = settings or get_settings()
        if not settings.openai_api_key:
            return GraphitiStatus(False, "OPENAI_API_KEY not set")
        return GraphitiStatus(False, "graphiti init failed (see logs)")
    return GraphitiStatus(True, "ready")
