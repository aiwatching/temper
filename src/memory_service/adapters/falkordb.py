"""FalkorDB connection management.

Graphiti talks to FalkorDB internally; we keep a separate raw client for
health checks. Using `falkordb-py` would pull in redis-py; we already have
that transitive dep via graphiti.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from memory_service.config import Settings, get_settings


@dataclass
class FalkorPing:
    ok: bool
    detail: str


async def ping_falkordb(settings: Settings | None = None) -> FalkorPing:
    """Best-effort liveness probe for FalkorDB.

    Returns `FalkorPing(ok=True)` when we can open a TCP connection and
    receive a PONG. We deliberately avoid importing the `falkordb` package
    here so the health endpoint stays useful even when that library is
    missing or misconfigured.
    """
    settings = settings or get_settings()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(settings.falkordb_host, settings.falkordb_port),
            timeout=2.0,
        )
    except Exception as exc:  # OSError, TimeoutError
        return FalkorPing(ok=False, detail=f"connect failed: {exc}")

    try:
        writer.write(b"PING\r\n")
        await writer.drain()
        data = await asyncio.wait_for(reader.readline(), timeout=2.0)
        if data.startswith(b"+PONG"):
            return FalkorPing(ok=True, detail="PONG")
        return FalkorPing(ok=False, detail=f"unexpected reply: {data!r}")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
