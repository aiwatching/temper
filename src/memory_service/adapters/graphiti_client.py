"""Graphiti client wrapper with multi-provider LLM + embedder support.

Construction is lazy and tolerant: if a required key is missing the client
isn't built and the health endpoint reports `degraded` instead of crashing
the app. This keeps the skeleton useful during partial bring-up.

Supported providers:

| LLM provider | Backed by                              |
|--------------|----------------------------------------|
| openai       | graphiti_core.llm_client.OpenAIClient   |
| deepseek     | OpenAIGenericClient (OpenAI-compatible) |
| anthropic    | AnthropicClient (extra dep)             |
| ollama       | OpenAIGenericClient pointing at 11434   |

| Embedder     |                                         |
|--------------|-----------------------------------------|
| openai       | OpenAIEmbedder                          |
| ollama       | OpenAIEmbedder pointing at 11434        |

Adding more (e.g. a `claude-cli` subprocess client) means subclassing
`graphiti_core.llm_client.LLMClient` — not done in this version.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from memory_service.config import ResolvedProvider, Settings, get_settings

_logger = logging.getLogger(__name__)


@dataclass
class ProviderStatus:
    name: str
    ok: bool
    detail: str


@dataclass
class GraphitiStatus:
    initialized: bool
    detail: str
    llm: ProviderStatus
    embedder: ProviderStatus


_client: object | None = None
_status_cache: GraphitiStatus | None = None


def _build_llm_client(rp: ResolvedProvider):  # type: ignore[no-untyped-def]
    """Return (client_or_None, ProviderStatus)."""
    if rp.needs_api_key and not rp.api_key:
        return None, ProviderStatus(rp.provider, False, "missing api key")

    try:
        from graphiti_core.llm_client import LLMConfig, OpenAIClient
        from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

        config = LLMConfig(
            api_key=rp.api_key or "ollama",  # Ollama ignores it
            model=rp.model,
            base_url=rp.base_url,
        )

        if rp.provider == "openai":
            return OpenAIClient(config=config), ProviderStatus(rp.provider, True, rp.model)
        if rp.provider in ("deepseek", "ollama"):
            return OpenAIGenericClient(config=config), ProviderStatus(rp.provider, True, rp.model)
        if rp.provider == "anthropic":
            try:
                import anthropic as anthropic_sdk
                from graphiti_core.llm_client.anthropic_client import AnthropicClient
            except ImportError as exc:
                return None, ProviderStatus(
                    rp.provider,
                    False,
                    f"anthropic extra not installed ({exc}); pip install graphiti-core[anthropic]",
                )
            # Standard API keys are `sk-ant-api03-…` and go in x-api-key.
            # Claude Code / OAuth tokens are `sk-ant-oat01-…` and need
            # Authorization: Bearer …, exposed by the SDK as `auth_token`.
            if rp.api_key and rp.api_key.startswith("sk-ant-oat"):
                sdk_client = anthropic_sdk.AsyncAnthropic(auth_token=rp.api_key)
                return (
                    AnthropicClient(config=config, client=sdk_client),
                    ProviderStatus(rp.provider, True, rp.model),
                )
            return AnthropicClient(config=config), ProviderStatus(rp.provider, True, rp.model)

        return None, ProviderStatus(rp.provider, False, f"unknown provider: {rp.provider}")
    except Exception as exc:  # pragma: no cover - defensive
        return None, ProviderStatus(rp.provider, False, f"init failed: {exc}")


def _build_embedder(rp: ResolvedProvider):  # type: ignore[no-untyped-def]
    if rp.needs_api_key and not rp.api_key:
        return None, ProviderStatus(rp.provider, False, "missing api key")

    try:
        from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig

        config = OpenAIEmbedderConfig(
            embedding_dim=rp.dimensions or 1536,
            embedding_model=rp.model,
            api_key=rp.api_key or "ollama",
            base_url=rp.base_url,
        )
        return OpenAIEmbedder(config=config), ProviderStatus(rp.provider, True, rp.model)
    except Exception as exc:  # pragma: no cover - defensive
        return None, ProviderStatus(rp.provider, False, f"init failed: {exc}")


def _make_noop_reranker():  # type: ignore[no-untyped-def]
    """Build a CrossEncoderClient that returns passages in original order.

    Used when no OpenAI-compatible chat endpoint is available to host the
    real reranker (e.g. anthropic LLM + ollama embedding-only setup).
    Search still works — results just aren't reordered against the query.

    Constructed inside a function so the import of CrossEncoderClient stays
    lazy (Graphiti might be importable but mis-configured at module load).
    """
    from graphiti_core.cross_encoder.client import CrossEncoderClient

    class _NoopReranker(CrossEncoderClient):
        async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
            return [(p, 1.0) for p in passages]

    return _NoopReranker()


def _build_reranker(llm_rp: ResolvedProvider, emb_rp: ResolvedProvider):  # type: ignore[no-untyped-def]
    """Construct a cross-encoder.

    Graphiti's `OpenAIRerankerClient` calls a chat endpoint to score
    passages. We point it at whichever provider speaks OpenAI chat:

      - openai / deepseek LLM         → reuse LLM endpoint (chat-capable)
      - ollama LLM                     → reuse LLM endpoint
      - anthropic LLM + openai embed   → reuse EMBEDDING endpoint, only if
                                          the embedding endpoint is the
                                          same gateway that also serves
                                          OpenAI-shaped chat
      - anthropic LLM + ollama embed   → no chat-capable source available
                                          (the embedder is `nomic-embed-text`
                                          which can't do chat). Skip
                                          reranking — return _NoopReranker.

    The Noop path keeps the service usable without forcing a chat key the
    user doesn't have; search returns the same set of facts, just not
    relevance-rescored by the cross-encoder.
    """
    source: ResolvedProvider | None = None
    if llm_rp.provider in ("openai", "deepseek", "ollama"):
        source = llm_rp
    elif llm_rp.provider == "anthropic" and emb_rp.provider == "openai":
        # OpenAI-compatible embedding endpoint is typically a gateway that
        # also serves chat. Worth trying.
        source = emb_rp

    if source is None or (source.needs_api_key and not source.api_key):
        _logger.info(
            "Using no-op reranker (no OpenAI-compatible chat endpoint available "
            "for llm=%s + embedding=%s)",
            llm_rp.provider,
            emb_rp.provider,
        )
        return _make_noop_reranker()

    try:
        from graphiti_core.cross_encoder.openai_reranker_client import (
            OpenAIRerankerClient,
        )
        from graphiti_core.llm_client import LLMConfig

        config = LLMConfig(
            api_key=source.api_key or "ollama",
            model=source.model,
            base_url=source.base_url,
        )
        return OpenAIRerankerClient(config=config)
    except Exception as exc:  # pragma: no cover - defensive
        _logger.warning("reranker init failed (%s); using no-op reranker", exc)
        return _make_noop_reranker()


def _build_graphiti(settings: Settings) -> tuple[object | None, GraphitiStatus]:
    llm_rp = settings.resolved_llm()
    emb_rp = settings.resolved_embedder()

    llm_client, llm_status = _build_llm_client(llm_rp)
    embedder, emb_status = _build_embedder(emb_rp)

    if llm_client is None or embedder is None:
        detail = []
        if llm_client is None:
            detail.append(f"llm: {llm_status.detail}")
        if embedder is None:
            detail.append(f"embedder: {emb_status.detail}")
        return None, GraphitiStatus(
            initialized=False,
            detail="; ".join(detail),
            llm=llm_status,
            embedder=emb_status,
        )

    cross_encoder = _build_reranker(llm_rp, emb_rp)

    try:
        from graphiti_core import Graphiti  # type: ignore[import-untyped]
        from graphiti_core.driver.falkordb_driver import (  # type: ignore[import-untyped]
            FalkorDriver,
        )

        # Don't set `database=` here: Graphiti's FalkorDB driver routes every
        # write to a graph named after the episode's `group_id`, so the
        # connection-level default is only used for low-level driver calls
        # that never carry data (indices, healthchecks). The lib's own
        # default ("default_db") is fine.
        driver = FalkorDriver(
            host=settings.falkordb_host,
            port=str(settings.falkordb_port),
            password=settings.falkordb_password,
        )
        client = Graphiti(
            graph_driver=driver,
            llm_client=llm_client,
            embedder=embedder,
            cross_encoder=cross_encoder,
        )
    except Exception as exc:
        _logger.error("Graphiti init failed: %s", exc)
        return None, GraphitiStatus(
            initialized=False,
            detail=f"Graphiti init failed: {exc}",
            llm=llm_status,
            embedder=emb_status,
        )

    return client, GraphitiStatus(
        initialized=True,
        detail="ready",
        llm=llm_status,
        embedder=emb_status,
    )


def get_graphiti(settings: Settings | None = None) -> object | None:
    """Return the cached Graphiti client, building it once."""
    global _client, _status_cache
    if _client is not None:
        return _client
    _client, _status_cache = _build_graphiti(settings or get_settings())
    return _client


def graphiti_status(settings: Settings | None = None) -> GraphitiStatus:
    global _client, _status_cache
    if _status_cache is None:
        _client, _status_cache = _build_graphiti(settings or get_settings())
    return _status_cache


def reset_graphiti_for_tests() -> None:
    """Drop cached state. Tests that monkeypatch settings should call this."""
    global _client, _status_cache
    _client = None
    _status_cache = None
