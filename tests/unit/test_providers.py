"""Provider resolution: defaults, overrides, and key fallbacks."""
from __future__ import annotations

import pytest

from memory_service.config import EMBEDDING_DEFAULTS, LLM_DEFAULTS, Settings


def _settings(**overrides: object) -> Settings:
    """Build a fresh Settings instance with all api keys forced to None.

    conftest seeds OPENAI_API_KEY / LLM_API_KEY into the environment for
    integration tests; these unit tests need to control exactly what each
    slot resolves to, so we explicitly null them out unless the caller
    overrides.
    """
    base: dict[str, object] = {
        "secret_key": "test-secret-key",
        "app_env": "test",
        "openai_api_key": None,
        "llm_api_key": None,
        "embedding_api_key": None,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def test_llm_defaults_per_provider() -> None:
    for provider, (base_url, model) in LLM_DEFAULTS.items():
        s = _settings(llm_provider=provider, llm_api_key="any-key")
        rp = s.resolved_llm()
        assert rp.provider == provider
        assert rp.base_url == base_url
        assert rp.model == model
        assert rp.api_key == "any-key"


def test_embedding_defaults_per_provider() -> None:
    for provider, (base_url, model, dim) in EMBEDDING_DEFAULTS.items():
        s = _settings(embedding_provider=provider, embedding_api_key="key")
        rp = s.resolved_embedder()
        assert rp.provider == provider
        assert rp.base_url == base_url
        assert rp.model == model
        assert rp.dimensions == dim


def test_llm_overrides_win_over_defaults() -> None:
    s = _settings(
        llm_provider="deepseek",
        llm_api_key="sk-ds-key",
        llm_base_url="https://custom-proxy/v1",
        llm_model="deepseek-coder-v2",
    )
    rp = s.resolved_llm()
    assert rp.base_url == "https://custom-proxy/v1"
    assert rp.model == "deepseek-coder-v2"
    assert rp.api_key == "sk-ds-key"


def test_legacy_openai_api_key_falls_back_only_for_openai() -> None:
    s_openai = _settings(llm_provider="openai", openai_api_key="sk-legacy")
    assert s_openai.resolved_llm().api_key == "sk-legacy"

    s_deepseek = _settings(llm_provider="deepseek", openai_api_key="sk-legacy")
    # DeepSeek must NOT pick up OPENAI_API_KEY by accident
    assert s_deepseek.resolved_llm().api_key is None


def test_ollama_does_not_need_api_key() -> None:
    s = _settings(llm_provider="ollama", embedding_provider="ollama")
    assert not s.resolved_llm().needs_api_key
    assert not s.resolved_embedder().needs_api_key


def test_mixed_setup_deepseek_llm_openai_embedding() -> None:
    """The common cost-optimised combo: cheap LLM + OpenAI embedding."""
    s = _settings(
        llm_provider="deepseek",
        llm_api_key="sk-ds",
        embedding_provider="openai",
        embedding_api_key="sk-oa",
    )
    llm = s.resolved_llm()
    emb = s.resolved_embedder()
    assert llm.provider == "deepseek"
    assert llm.base_url == "https://api.deepseek.com/v1"
    assert emb.provider == "openai"
    assert emb.base_url == "https://api.openai.com/v1"


@pytest.mark.parametrize(
    "provider",
    ["openai", "deepseek", "anthropic"],
)
def test_provider_without_api_key_is_flagged_as_missing(provider: str) -> None:
    s = _settings(llm_provider=provider)  # no llm_api_key, no openai_api_key
    rp = s.resolved_llm()
    assert rp.needs_api_key
    assert rp.api_key is None
