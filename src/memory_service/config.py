"""Application configuration.

All runtime config flows through `Settings`, which reads from environment
variables (and `.env` for local development). Modules NEVER import os.environ
directly — they receive `Settings` via dependency injection so tests can
override.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    secret_key: str = Field(default="dev-insecure-change-me", min_length=8)

    # --- PostgreSQL ---
    # SQLAlchemy URL. For tests we override to in-memory SQLite.
    database_url: str = "postgresql+asyncpg://memory:memory@localhost:5432/memory_service"

    # --- FalkorDB ---
    falkordb_host: str = "localhost"
    falkordb_port: int = 6379
    falkordb_password: str | None = None
    falkordb_graph_name: str = "memory"

    # --- OpenAI / LLM ---
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"

    # --- Bootstrap ---
    bootstrap_super_admin_email: str | None = None

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"

    @property
    def is_test(self) -> bool:
        return self.app_env == "test"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
