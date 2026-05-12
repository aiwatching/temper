"""FastAPI application entrypoint.

Phase 1.1 surface: /v1/health + /admin + auto-generated /docs. Auth, episodes,
search etc. plug in via additional routers in later phases.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from memory_service.api.v1 import system as v1_system
from memory_service.config import get_settings
from memory_service.db.session import get_database, init_database
from memory_service.web.router import router as admin_router

_logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    init_database(settings)
    _logger.info("memory-service starting (env=%s)", settings.app_env)
    try:
        yield
    finally:
        await get_database().dispose()
        _logger.info("memory-service shut down cleanly")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Memory Service",
        version="0.1.0",
        description="Multi-tenant central memory layer for AI agents.",
        lifespan=lifespan,
    )

    # v1 API
    app.include_router(v1_system.router, prefix="/v1")

    # Admin page + its static assets
    app.include_router(admin_router)
    static_dir = Path(__file__).parent / "web" / "static"
    app.mount("/admin/static", StaticFiles(directory=str(static_dir)), name="admin-static")

    # Convenience redirect for the impatient
    from fastapi.responses import RedirectResponse

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/admin")

    _ = settings  # silence unused while lifespan owns settings
    return app


app = create_app()


def cli() -> None:
    """`memory-service` console script entrypoint.

    Thin shim so `pip install -e .` users get a binary; production deployments
    should run uvicorn / gunicorn directly per Dockerfile.
    """
    import uvicorn

    uvicorn.run("memory_service.main:app", host="0.0.0.0", port=8000, reload=False)
