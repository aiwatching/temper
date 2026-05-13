"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from memory_service.api.v1 import auth as v1_auth
from memory_service.api.v1 import episodes as v1_episodes
from memory_service.api.v1 import search as v1_search
from memory_service.api.v1 import system as v1_system
from memory_service.api.v1 import users as v1_users
from memory_service.config import get_settings
from memory_service.db.session import get_database, init_database
from memory_service.web.router import router as admin_router

_logger = logging.getLogger(__name__)


async def _ensure_schema_for_test() -> None:
    """Best-effort schema bootstrap for in-memory / test DBs.

    Production runs `alembic upgrade head` out-of-band; this exists so the
    app boots cleanly when pointed at `sqlite+aiosqlite:///:memory:` (tests)
    without needing an extra fixture.
    """
    settings = get_settings()
    if not settings.database_url.startswith("sqlite"):
        return
    from memory_service.models import Base

    db = get_database()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _run_bootstrap() -> None:
    """Promote BOOTSTRAP_SUPER_ADMIN_EMAIL on startup if that user exists."""
    from memory_service.core.bootstrap import promote_bootstrap_super_admin

    settings = get_settings()
    db = get_database()
    async for session in db.session():
        await promote_bootstrap_super_admin(settings, session)
        break


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    init_database(settings)
    await _ensure_schema_for_test()
    await _run_bootstrap()
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
    app.include_router(v1_auth.router, prefix="/v1")
    app.include_router(v1_users.router, prefix="/v1")
    app.include_router(v1_episodes.router, prefix="/v1")
    app.include_router(v1_search.router, prefix="/v1")

    # Admin page + static
    app.include_router(admin_router)
    static_dir = Path(__file__).parent / "web" / "static"
    app.mount("/admin/static", StaticFiles(directory=str(static_dir)), name="admin-static")

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/admin")

    _ = settings  # silence unused — lifespan owns it
    return app


app = create_app()


def cli() -> None:
    import uvicorn

    uvicorn.run("memory_service.main:app", host="0.0.0.0", port=8000, reload=False)
