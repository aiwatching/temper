"""Pytest fixtures shared across unit + integration tests."""
from __future__ import annotations

import os

import pytest

# Force test env early so any module-level settings reads pick it up.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "test-secret-do-not-use-in-prod")


@pytest.fixture
def app():
    """Fresh FastAPI app per test. Avoids state bleed between tests."""
    from memory_service.main import create_app

    return create_app()


@pytest.fixture
async def client(app):  # type: ignore[no-untyped-def]
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
