"""Env-driven config. Mirrors TEMPER's settings.py style — pydantic-settings
+ a single get_settings() with lru_cache so every module sees the same view.
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
    )

    # --- Temper ---
    temper_base_url: str = "http://127.0.0.1:18088"
    temper_api_key: str = Field(default="", description="X-API-Key for Temper")

    # --- LLM ---
    llm_provider: Literal["anthropic", "openai", "deepseek", "ollama"] = "anthropic"
    llm_api_key: str = ""
    llm_model: str = "claude-sonnet-4-6"

    # --- MCP ---
    # Comma-separated `name=URL` pairs. Parsed lazily by smith.mcp.
    mcp_servers: str = ""

    # --- Smith control plane ---
    smith_host: str = "127.0.0.1"
    smith_port: int = 18099


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
