"""System endpoints — health, version. No auth required."""
from __future__ import annotations

import time
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Query

from memory_service.adapters.falkordb import ping_falkordb
from memory_service.adapters.graphiti_client import graphiti_status
from memory_service.config import ResolvedProvider, get_settings
from memory_service.db.session import get_database

router = APIRouter(tags=["system"])

# Per-probe HTTP timeout. Live LLM/embedder probes use this — long
# enough to forgive a slow first cold-start response, short enough
# to actually fail fast when the endpoint is unreachable. 10s is the
# sweet spot for human debugging; if you want a real load balancer
# health check, use the shallow path (no ?deep=true).
PROBE_TIMEOUT_SECONDS = 10.0


@router.get("/health")
async def health(
    deep: Annotated[
        bool,
        Query(
            description=(
                "If true, actually call the configured LLM + embedder "
                "endpoints with a tiny prompt to verify they're "
                "reachable from inside this container. Default false "
                "uses cached status set at boot — fast enough for k8s "
                "liveness probes but won't notice a downstream that "
                "broke after startup."
            ),
        ),
    ] = False,
) -> dict[str, object]:
    """Aggregate health probe. Always returns 200; inspect the body for status.

    Two modes:

      * `/v1/health` — shallow + cached. Checks postgres + falkordb
        liveness on each call, but LLM / embedder are the construction-
        time status saved at boot. Fine for `is the service alive?`
        but blind to a downstream provider that broke later.

      * `/v1/health?deep=true` — live probe. Makes a real (tiny)
        request to the LLM's chat completions endpoint and the
        embedder's embeddings endpoint. Reveals network reachability
        + auth + cold-start issues that the cached path misses.
        Costs one cheap LLM call + one cheap embedding per request,
        so don't wire it into a 5s liveness probe.
    """
    settings = get_settings()
    db = get_database()

    db_ok = await db.ping()
    falkor = await ping_falkordb(settings)
    g = graphiti_status(settings)

    llm_block: dict[str, Any] = {
        "provider": g.llm.name,
        "ok": g.llm.ok,
        "detail": g.llm.detail,
    }
    embedder_block: dict[str, Any] = {
        "provider": g.embedder.name,
        "ok": g.embedder.ok,
        "detail": g.embedder.detail,
    }

    if deep:
        # Replace the cached "ok" with a real-world probe result so
        # the response top-level reflects reality. We keep the cached
        # detail under `cached_detail` for comparison.
        llm_live = await _probe_llm(settings.resolved_llm())
        emb_live = await _probe_embedder(settings.resolved_embedder())
        llm_block = {
            "provider": llm_live["provider"],
            "ok": llm_live["ok"],
            "detail": llm_live["detail"],
            "elapsed_ms": llm_live["elapsed_ms"],
            "probe": "live",
            "cached_ok": g.llm.ok,
        }
        embedder_block = {
            "provider": emb_live["provider"],
            "ok": emb_live["ok"],
            "detail": emb_live["detail"],
            "elapsed_ms": emb_live["elapsed_ms"],
            "probe": "live",
            "cached_ok": g.embedder.ok,
        }

    graphiti_ok = g.initialized and llm_block["ok"] and embedder_block["ok"]
    overall_ok = db_ok and falkor.ok and graphiti_ok

    return {
        "status": "ok" if overall_ok else "degraded",
        "version": "0.1.0",
        "env": settings.app_env,
        "probe_mode": "deep" if deep else "shallow",
        "checks": {
            "postgres": {"ok": db_ok},
            "falkordb": {"ok": falkor.ok, "detail": falkor.detail},
            "graphiti": {
                "ok": graphiti_ok,
                "detail": g.detail,
                "llm": llm_block,
                "embedder": embedder_block,
            },
        },
    }


async def _probe_llm(rp: ResolvedProvider) -> dict[str, Any]:
    """Live chat completion ping.

    Sends the shortest possible request the OpenAI-shaped API accepts
    and reports whether we got a response. Designed to fail loudly:
    surface exact exception type + message so the caller knows
    whether it's network, auth, or model-not-found.

    Anthropic gets a separate code path because its request shape
    differs. Everything else (openai / deepseek / ollama / corp
    OpenAI-compat gateways) shares the chat-completions path."""
    if rp.needs_api_key and not rp.api_key:
        return _probe_result(rp, ok=False, detail="missing api key", elapsed_ms=0)

    if rp.provider == "anthropic":
        return await _probe_anthropic(rp)

    url = rp.base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {rp.api_key or 'ollama'}",
        "Content-Type": "application/json",
    }
    body = {
        "model": rp.model,
        "messages": [{"role": "user", "content": "."}],
        "max_tokens": 1,
        "temperature": 0,
    }
    return await _http_probe(rp, url, headers, body)


async def _probe_anthropic(rp: ResolvedProvider) -> dict[str, Any]:
    """Anthropic uses x-api-key + /v1/messages, not /chat/completions."""
    base = rp.base_url.rstrip("/") if rp.base_url else "https://api.anthropic.com"
    url = base + "/v1/messages"
    # OAuth tokens (sk-ant-oat...) use Authorization: Bearer; standard
    # API keys (sk-ant-api...) use the x-api-key header.
    if rp.api_key and rp.api_key.startswith("sk-ant-oat"):
        headers = {
            "Authorization": f"Bearer {rp.api_key}",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
    else:
        headers = {
            "x-api-key": rp.api_key or "",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
    body = {
        "model": rp.model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "."}],
    }
    return await _http_probe(rp, url, headers, body)


async def _probe_embedder(rp: ResolvedProvider) -> dict[str, Any]:
    """Live embedding ping. OpenAI-shaped /embeddings request."""
    if rp.needs_api_key and not rp.api_key:
        return _probe_result(rp, ok=False, detail="missing api key", elapsed_ms=0)

    url = rp.base_url.rstrip("/") + "/embeddings"
    headers = {
        "Authorization": f"Bearer {rp.api_key or 'ollama'}",
        "Content-Type": "application/json",
    }
    body = {"model": rp.model, "input": "."}
    return await _http_probe(rp, url, headers, body)


async def _http_probe(
    rp: ResolvedProvider,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
) -> dict[str, Any]:
    """POST + classify the outcome. Returns the shape _probe_result
    produces so callers don't need to branch."""
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, headers=headers, json=body)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if resp.status_code < 400:
            return _probe_result(
                rp, ok=True,
                detail=f"{rp.model} responded {resp.status_code} ({len(resp.content)}B)",
                elapsed_ms=elapsed_ms,
            )
        # Truncate body — error responses can be huge HTML pages.
        text = resp.text[:200].replace("\n", " ")
        return _probe_result(
            rp, ok=False,
            detail=f"HTTP {resp.status_code} from {url}: {text}",
            elapsed_ms=elapsed_ms,
        )
    except httpx.ConnectError as exc:
        return _probe_result(
            rp, ok=False,
            detail=f"ConnectError reaching {url}: {exc}",
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
    except httpx.TimeoutException as exc:
        return _probe_result(
            rp, ok=False,
            detail=f"timeout after {PROBE_TIMEOUT_SECONDS}s talking to {url}: {exc}",
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception as exc:  # noqa: BLE001 — health endpoint must not throw
        return _probe_result(
            rp, ok=False,
            detail=f"{type(exc).__name__}: {exc}",
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )


def _probe_result(
    rp: ResolvedProvider, *, ok: bool, detail: str, elapsed_ms: int,
) -> dict[str, Any]:
    return {
        "provider": rp.provider,
        "ok": ok,
        "detail": detail,
        "elapsed_ms": elapsed_ms,
    }
