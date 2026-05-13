"""Application configuration.

All runtime config flows through `Settings`, which reads from environment
variables (and `.env` for local development). Modules NEVER import os.environ
directly — they receive `Settings` via dependency injection so tests can
override.

Two LLM-shaped configurations live here: one for the extraction/reasoning
LLM and one for the embedder. They are independent on purpose — a common
setup is "DeepSeek for extraction (cheap, no embedding API) + OpenAI for
embedding".
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["openai", "deepseek", "anthropic", "ollama"]
EmbeddingProvider = Literal["openai", "ollama"]

# Sensible per-provider defaults. Each entry is (base_url, model).
# These get filled in only if the user didn't override LLM_BASE_URL / LLM_MODEL.
LLM_DEFAULTS: dict[LLMProvider, tuple[str, str]] = {
    "openai":    ("https://api.openai.com/v1",       "gpt-4o-mini"),
    "deepseek":  ("https://api.deepseek.com/v1",     "deepseek-chat"),
    "anthropic": ("https://api.anthropic.com",       "claude-3-5-haiku-latest"),
    "ollama":    ("http://localhost:11434/v1",       "qwen2.5:14b-instruct"),
}

EMBEDDING_DEFAULTS: dict[EmbeddingProvider, tuple[str, str, int]] = {
    "openai": ("https://api.openai.com/v1",       "text-embedding-3-small", 1536),
    "ollama": ("http://localhost:11434/v1",       "nomic-embed-text",       768),
}


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
    database_url: str = "postgresql+asyncpg://memory:memory@localhost:5432/memory_service"

    # --- FalkorDB ---
    falkordb_host: str = "localhost"
    falkordb_port: int = 6379
    falkordb_password: str | None = None
    falkordb_graph_name: str = "memory"

    # --- LLM (for entity / relation extraction) ---
    llm_provider: LLMProvider = "openai"
    # If left empty, falls back to LLM_DEFAULTS[provider]
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_temperature: float = 0.0
    llm_max_tokens: int = 8192

    # --- Embedding (for semantic search) ---
    embedding_provider: EmbeddingProvider = "openai"
    embedding_api_key: str | None = None
    embedding_base_url: str | None = None
    embedding_model: str | None = None
    embedding_dimensions: int | None = None  # auto from defaults

    # --- Bootstrap ---
    bootstrap_super_admin_email: str | None = None

    # --- Legacy aliases (so older OPENAI_* env vars still work) ---
    # OPENAI_API_KEY → llm_api_key fallback for the openai provider
    openai_api_key: str | None = None

    # ---------- derived helpers ----------

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"

    @property
    def is_test(self) -> bool:
        return self.app_env == "test"

    def resolved_llm(self) -> "ResolvedProvider":
        """Compose the active LLM config with provider defaults filled in."""
        base_url_default, model_default = LLM_DEFAULTS[self.llm_provider]
        api_key = self.llm_api_key or (
            self.openai_api_key if self.llm_provider == "openai" else None
        )
        return ResolvedProvider(
            provider=self.llm_provider,
            api_key=api_key,
            base_url=self.llm_base_url or base_url_default,
            model=self.llm_model or model_default,
        )

    def resolved_embedder(self) -> "ResolvedProvider":
        base_url_default, model_default, dim_default = EMBEDDING_DEFAULTS[self.embedding_provider]
        api_key = self.embedding_api_key or (
            self.openai_api_key if self.embedding_provider == "openai" else None
        )
        return ResolvedProvider(
            provider=self.embedding_provider,
            api_key=api_key,
            base_url=self.embedding_base_url or base_url_default,
            model=self.embedding_model or model_default,
            dimensions=self.embedding_dimensions or dim_default,
        )


from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedProvider:
    """A flattened, defaults-applied view of a provider config slot."""

    provider: str
    api_key: str | None
    base_url: str
    model: str
    dimensions: int | None = None

    @property
    def needs_api_key(self) -> bool:
        return self.provider != "ollama"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
