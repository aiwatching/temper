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
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _empty_to_none(v: Any) -> Any:
    """Treat empty strings (from blank .env entries) as unset."""
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


_NoneIfEmptyInt = Annotated[int | None, BeforeValidator(_empty_to_none)]
_NoneIfEmptyStr = Annotated[str | None, BeforeValidator(_empty_to_none)]

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
    # `text` for human-readable (dev / interactive), `json` for shipping
    # to ELK / loki / datadog. Pick `json` in production env files.
    log_format: Literal["text", "json"] = "text"
    secret_key: str = Field(default="dev-insecure-change-me", min_length=8)

    # Comma-separated origin allowlist for CORS. Empty = no CORS middleware,
    # which means browsers will block any cross-origin call (safe default).
    # Use "*" only when iterating locally; production should list explicit
    # origins so credentialed requests stay scoped.
    cors_allow_origins: str = ""

    # Self-registration policy. Default = False — onboarding flows
    # only through admin-issued invites (POST /v1/users + invite token).
    # Set True ONLY for open-signup demos. The admin UI no longer
    # surfaces a Register button regardless of this flag; toggling it
    # only affects whether POST /v1/auth/register is reachable.
    allow_self_registration: bool = False

    # How long an invite token stays valid. 24h matches most enterprise
    # SSO invite norms. Operators with stricter SLOs can shorten this.
    invite_ttl_hours: int = 24

    # On first boot (empty users table), auto-create a default super_admin
    # so operators don't have to find a setup wizard. Disable in deploys
    # that bootstrap via /v1/auth/setup/initial-admin or a config job.
    create_default_admin: bool = True
    default_admin_email: str = "admin@example.com"
    default_admin_username: str = "admin"
    default_admin_password: str = "admin"

    # Default password handed to every admin-created user. Since this
    # service doesn't send email, the admin tells the new user the
    # password out-of-band; the user is force-changed on first login.
    # Override if your org has a stricter "starter password" policy.
    default_new_user_password: str = "12345678"

    # --- PostgreSQL ---
    database_url: str = "postgresql+asyncpg://memory:memory@localhost:5432/memory_service"

    # --- FalkorDB ---
    falkordb_host: str = "localhost"
    falkordb_port: int = 6379
    falkordb_password: _NoneIfEmptyStr = None

    # --- Search ---
    # Default reranker for /v1/search. rrf is free; cross_encoder uses
    # the configured LLM for each query (slower + costs tokens); mmr is
    # diversity-biased rather than relevance-biased.
    search_reranker: Literal["rrf", "cross_encoder", "mmr"] = "rrf"

    # --- LLM (for entity / relation extraction) ---
    llm_provider: LLMProvider = "openai"
    # If left empty, falls back to LLM_DEFAULTS[provider]
    llm_api_key: _NoneIfEmptyStr = None
    llm_base_url: _NoneIfEmptyStr = None
    llm_model: _NoneIfEmptyStr = None
    llm_temperature: float = 0.0
    llm_max_tokens: int = 8192

    # --- Embedding (for semantic search) ---
    embedding_provider: EmbeddingProvider = "openai"
    embedding_api_key: _NoneIfEmptyStr = None
    embedding_base_url: _NoneIfEmptyStr = None
    embedding_model: _NoneIfEmptyStr = None
    embedding_dimensions: _NoneIfEmptyInt = None  # auto from defaults

    # --- Auth / sessions ---
    # JWT signing algorithm and lifetime for /v1/auth/login tokens.
    jwt_algorithm: str = "HS256"
    session_lifetime_minutes: int = 60 * 24  # 1 day

    # --- Bootstrap ---
    bootstrap_super_admin_email: _NoneIfEmptyStr = None

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
        return ResolvedProvider(
            provider=self.llm_provider,
            api_key=self.llm_api_key,
            base_url=self.llm_base_url or base_url_default,
            model=self.llm_model or model_default,
        )

    def resolved_embedder(self) -> "ResolvedProvider":
        base_url_default, model_default, dim_default = EMBEDDING_DEFAULTS[self.embedding_provider]
        return ResolvedProvider(
            provider=self.embedding_provider,
            api_key=self.embedding_api_key,
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
