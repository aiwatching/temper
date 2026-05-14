"""Smith — personal company-level agent.

A long-running local process that:
  - talks to TEMPER (memory) over HTTP via `smith.temper`
  - talks to internal MCP servers via `smith.mcp` (TBD wiring)
  - drives an LLM loop in `smith.agent`
  - exposes a tiny HTTP control plane in `smith.server`

Co-designed with TEMPER — when the agent wants a primitive memory
doesn't expose yet, we add it to TEMPER first, then use it here. The
agent is a Temper client today; nothing in here imports memory_service.
"""
__version__ = "0.0.1"
