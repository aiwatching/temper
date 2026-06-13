"""FalkorDB connection management.

The health probe verifies that the endpoint **actually serves the GRAPH
module** — a plain Redis would happily reply to PING but blow up the
first time Graphiti calls GRAPH.QUERY at runtime. We catch that here so
/v1/health is honest.

We deliberately avoid importing the `falkordb` package; using raw RESP
over a TCP socket keeps this probe usable even when client libs are
missing or misconfigured.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from memory_service.config import Settings, get_settings


@dataclass
class FalkorPing:
    ok: bool
    detail: str


def _encode_command(*args: str) -> bytes:
    """Encode a Redis RESP-2 array command."""
    out = bytearray(f"*{len(args)}\r\n".encode())
    for a in args:
        b = a.encode("utf-8")
        out += f"${len(b)}\r\n".encode()
        out += b
        out += b"\r\n"
    return bytes(out)


async def _read_response(reader: asyncio.StreamReader) -> tuple[bytes, bytes]:
    """Read one RESP response and return (kind_byte, payload_first_line)."""
    line = await asyncio.wait_for(reader.readline(), timeout=2.0)
    if not line:
        return (b"", b"")
    return (line[:1], line[1:].rstrip(b"\r\n"))


async def falkordb_bgsave(settings: Settings | None = None) -> tuple[bool, str]:
    """Trigger a FalkorDB/Redis BGSAVE and wait for it to finish.

    Used by the in-app backup: after this returns ok, the RDB on disk
    is current and the service can copy it from the read-only data
    mount. Returns (ok, detail). Best-effort — a failure here just
    means the backup is Postgres-only.
    """
    settings = settings or get_settings()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(settings.falkordb_host, settings.falkordb_port),
            timeout=5.0,
        )
    except Exception as exc:
        return (False, f"connect failed: {exc}")

    try:
        writer.write(_encode_command("BGSAVE"))
        await writer.drain()
        kind, payload = await _read_response(reader)
        if kind == b"-":
            return (False, payload.decode("utf-8", errors="replace"))

        # Poll INFO persistence until rdb_bgsave_in_progress:0.
        for _ in range(60):  # ~30s
            await asyncio.sleep(0.5)
            writer.write(_encode_command("INFO", "persistence"))
            await writer.drain()
            kind, first = await _read_response(reader)
            if kind != b"$":
                continue
            try:
                n = int(first)
            except ValueError:
                n = -1
            body = b""
            if n > 0:
                body = await asyncio.wait_for(reader.readexactly(n + 2), timeout=5.0)
            text = body.decode("utf-8", errors="replace")
            if "rdb_bgsave_in_progress:0" in text:
                return (True, "bgsave complete")
        return (True, "bgsave triggered (completion not confirmed)")
    except Exception as exc:
        return (False, f"bgsave error: {exc}")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def ping_falkordb(settings: Settings | None = None) -> FalkorPing:
    """Liveness + capability probe for FalkorDB.

    Returns ok=True only when the endpoint actually serves `GRAPH.QUERY`.
    A plain Redis that lacks the FalkorDB module will return an error
    here; we report that explicitly instead of pretending it's healthy.
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
        # Use a throwaway graph name so we don't pollute the user's data.
        writer.write(_encode_command("GRAPH.QUERY", "_healthcheck", "RETURN 1"))
        await writer.drain()
        kind, payload = await _read_response(reader)
        if kind == b"-":
            # Error response — usually "unknown command 'GRAPH.QUERY'" on
            # plain redis, or a genuine FalkorDB error string.
            text = payload.decode("utf-8", errors="replace")
            if "unknown command" in text.lower():
                return FalkorPing(
                    ok=False,
                    detail="endpoint is plain Redis, not FalkorDB (no GRAPH module)",
                )
            return FalkorPing(ok=False, detail=f"GRAPH.QUERY error: {text}")
        if kind == b"*":
            return FalkorPing(ok=True, detail="GRAPH.QUERY ok")
        return FalkorPing(ok=False, detail=f"unexpected reply: {kind!r} {payload!r}")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
