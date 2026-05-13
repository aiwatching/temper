"""System endpoints — health, version. No auth required."""
from __future__ import annotations

from fastapi import APIRouter

from memory_service.adapters.falkordb import ping_falkordb
from memory_service.adapters.graphiti_client import graphiti_status
from memory_service.config import get_settings
from memory_service.db.session import get_database

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, object]:
    """Aggregate health probe. Always returns 200; inspect the body for status.

    Monitoring should check `status` and the per-component `checks` rather
    than relying on the HTTP code — a degraded LLM provider shouldn't make
    the rest of the service look down.
    """
    settings = get_settings()
    db = get_database()

    db_ok = await db.ping()
    falkor = await ping_falkordb(settings)
    g = graphiti_status(settings)

    overall_ok = db_ok and falkor.ok and g.initialized
    return {
        "status": "ok" if overall_ok else "degraded",
        "version": "0.1.0",
        "env": settings.app_env,
        "checks": {
            "postgres": {"ok": db_ok},
            "falkordb": {"ok": falkor.ok, "detail": falkor.detail},
            "graphiti": {
                "ok": g.initialized,
                "detail": g.detail,
                "llm": {
                    "provider": g.llm.name,
                    "ok": g.llm.ok,
                    "detail": g.llm.detail,
                },
                "embedder": {
                    "provider": g.embedder.name,
                    "ok": g.embedder.ok,
                    "detail": g.embedder.detail,
                },
            },
        },
    }
