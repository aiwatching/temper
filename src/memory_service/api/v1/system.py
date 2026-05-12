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
    """Aggregate health probe. Always returns 200; check the body for status.

    A 200 with `status: degraded` is intentional — we want monitoring to see
    *which* dependency is down rather than just a generic 503.
    """
    settings = get_settings()
    db = get_database()

    db_ok = await db.ping()
    falkor = await ping_falkordb(settings)
    gstatus = graphiti_status(settings)

    overall_ok = db_ok and falkor.ok and gstatus.initialized
    return {
        "status": "ok" if overall_ok else "degraded",
        "version": "0.1.0",
        "env": settings.app_env,
        "checks": {
            "postgres": {"ok": db_ok},
            "falkordb": {"ok": falkor.ok, "detail": falkor.detail},
            "graphiti": {"ok": gstatus.initialized, "detail": gstatus.detail},
        },
    }
