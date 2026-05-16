"""FastAPI application entrypoint."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from memory_service.api.v1 import admin as v1_admin
from memory_service.api.v1 import auth as v1_auth
from memory_service.api.v1 import blocks as v1_blocks
from memory_service.api.v1 import consolidate as v1_consolidate
from memory_service.api.v1 import entity_schemas as v1_entity_schemas
from memory_service.api.v1 import episodes as v1_episodes
from memory_service.api.v1 import graph as v1_graph
from memory_service.api.v1 import graph_items as v1_graph_items
from memory_service.api.v1 import groups as v1_groups
from memory_service.api.v1 import namespaces as v1_namespaces
from memory_service.api.v1 import orgs as v1_orgs
from memory_service.api.v1 import sagas as v1_sagas
from memory_service.api.v1 import search as v1_search
from memory_service.api.v1 import stats as v1_stats
from memory_service.api.v1 import system as v1_system
from memory_service.api.v1 import typed_memory as v1_typed_memory
from memory_service.api.v1 import user_admin as v1_user_admin
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
    """Run the order-sensitive startup bootstrap chain:

    1. Seed default admin (only fires on empty DB).
    2. Promote BOOTSTRAP_SUPER_ADMIN_EMAIL if that user already exists.
    """
    from memory_service.core.bootstrap import (
        create_default_admin_if_empty,
        promote_bootstrap_super_admin,
    )

    settings = get_settings()
    db = get_database()
    async for session in db.session():
        await create_default_admin_if_empty(settings, session)
        await promote_bootstrap_super_admin(settings, session)
        break


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    settings = get_settings()
    from memory_service.logging_config import configure_logging

    configure_logging(level=settings.log_level, fmt=settings.log_format)
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

    # CORS: comma-separated allowlist from CORS_ALLOW_ORIGINS env. Disabled
    # entirely if empty so we don't accidentally ship a permissive
    # default. Pass "*" only for dev.
    if settings.cors_allow_origins:
        origins = [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
        if origins:
            app.add_middleware(
                CORSMiddleware,
                allow_origins=origins,
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )
            _logger.info("CORS enabled for origins: %s", origins)

    # v1 API
    app.include_router(v1_system.router, prefix="/v1")
    app.include_router(v1_auth.router, prefix="/v1")
    app.include_router(v1_users.router, prefix="/v1")
    app.include_router(v1_users.admin_router, prefix="/v1")
    app.include_router(v1_user_admin.router, prefix="/v1")
    app.include_router(v1_episodes.router, prefix="/v1")
    app.include_router(v1_search.router, prefix="/v1")
    app.include_router(v1_orgs.router, prefix="/v1")
    app.include_router(v1_groups.router, prefix="/v1")
    app.include_router(v1_graph.router, prefix="/v1")
    app.include_router(v1_graph_items.router, prefix="/v1")
    app.include_router(v1_namespaces.router, prefix="/v1")
    app.include_router(v1_admin.router, prefix="/v1")
    app.include_router(v1_sagas.router, prefix="/v1")
    app.include_router(v1_entity_schemas.router, prefix="/v1")
    app.include_router(v1_stats.router, prefix="/v1")
    app.include_router(v1_consolidate.router, prefix="/v1")
    app.include_router(v1_blocks.router, prefix="/v1")
    app.include_router(v1_typed_memory.router, prefix="/v1")

    # Admin page + static
    app.include_router(admin_router)
    static_dir = Path(__file__).parent / "web" / "static"
    app.mount("/admin/static", StaticFiles(directory=str(static_dir)), name="admin-static")

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/admin")

    # /healthz top-level alias for monitoring probes that default to
    # the conventional path. Delegates to the /v1/health handler so
    # the response shape stays identical.
    @app.get("/healthz", include_in_schema=False, tags=["system"])
    async def healthz() -> dict[str, object]:
        return await v1_system.health()

    _ = settings  # silence unused — lifespan owns it
    return app


app = create_app()


def cli() -> None:
    import uvicorn

    uvicorn.run("memory_service.main:app", host="0.0.0.0", port=18088, reload=False)
