"""MCP integration scaffolding.

The whole point of Smith for this team is that internal services already
expose MCP. This module is the wire-up point. Today it's a placeholder
because the agent loop in `smith.agent` is also a placeholder — once we
pick a framework (see docs/framework-comparison.md) we slot in:

  - the framework's preferred MCP client adapter, OR
  - the official `mcp` Python package, calling the framework's tool API

What we DO commit to here is the shape:

  parse_mcp_servers()        — read MCP_SERVERS env var into a list of
                               (name, transport_url) tuples
  open_clients(servers)      — open one MCP client per server, returned
                               as an async context manager so the agent
                               loop can keep them connected for the
                               session and close cleanly on shutdown

Once wired, `list_tools(clients)` returns a flat list of tool specs
that the LLM loop merges with Temper's own tools (memory.write,
memory.search).
"""
from __future__ import annotations

from dataclasses import dataclass

from smith.config import get_settings


@dataclass(frozen=True)
class MCPServer:
    name: str
    transport_url: str  # http(s)://… or stdio:///…


def parse_mcp_servers() -> list[MCPServer]:
    """Parse the MCP_SERVERS env var. Each comma-separated entry is
    `name=URL`. Empty / malformed entries are skipped silently — Smith
    should still start without MCP available."""
    raw = get_settings().mcp_servers or ""
    out: list[MCPServer] = []
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece or "=" not in piece:
            continue
        name, url = piece.split("=", 1)
        name = name.strip()
        url = url.strip()
        if not name or not url:
            continue
        out.append(MCPServer(name=name, transport_url=url))
    return out


# Placeholder. Will become:
#   async def open_clients(servers): ...   yields {name: McpClient}
#   async def list_tools(clients): ...     returns the merged tool spec list
#
# Implementation depends on the framework choice. Holding off so we don't
# write code we'll throw away.
