"""Pytest fixtures shared across unit + integration tests.

Each test gets its own temp-file SQLite DB so async engines don't share
state between tests (which produced flakey CancelledError on close).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest_asyncio

# Force test env BEFORE any memory_service import reads it.
os.environ["APP_ENV"] = "test"
os.environ["SECRET_KEY"] = "test-secret-do-not-use-in-prod"
# Stub LLM key so Graphiti init paths in tests don't trip on missing creds.
os.environ.setdefault("LLM_API_KEY", "test")
os.environ.setdefault("EMBEDDING_API_KEY", "test")
# Existing tests rely on /v1/auth/register to seed users quickly.
# The production default flipped to invite-only; explicitly enable
# self-reg for the test harness so we don't have to rewrite every
# fixture through the admin/invite flow.
os.environ["ALLOW_SELF_REGISTRATION"] = "true"
# Skip the default-admin seed so the tests' "register first user" path
# gets a clean DB. Otherwise every test fixture would start with one
# extra admin@example.com row, which a few of the assertions depend on
# *not* being there.
os.environ["CREATE_DEFAULT_ADMIN"] = "false"


@pytest_asyncio.fixture
async def client(tmp_path: Path):  # type: ignore[no-untyped-def]
    """Fresh FastAPI app + isolated SQLite file per test.

    `httpx.ASGITransport` doesn't fire FastAPI lifespan events by default,
    so we (a) reset module-level singletons and (b) create the schema
    directly here before serving any requests.
    """
    db_file = tmp_path / "test.db"
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_file}"

    from memory_service.config import get_settings
    from memory_service.db import session as db_session_mod

    get_settings.cache_clear()
    db_session_mod._db = None  # type: ignore[attr-defined]

    # Create DB schema up-front.
    from memory_service.db.session import init_database
    from memory_service.models import Base

    db = init_database(get_settings())
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    from httpx import ASGITransport, AsyncClient

    from memory_service.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        await db.dispose()
