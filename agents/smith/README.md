# smith — personal company-level agent

Long-running local process that drives an LLM tool-use loop with two
big external surfaces:

- **TEMPER** for memory (the service in the parent repo).
- **MCP** for everything else (your company exposes systems via MCP).

`smith` is a TEMPER *client*. It only calls documented `/v1/` endpoints
— if it needs a primitive memory doesn't expose yet, we add it to
TEMPER first.

## Status

MVP scaffold. The agent loop is a placeholder (see
`docs/framework-comparison.md` for the framework decision still to be
made). What works today:

- `GET  /healthz` — checks Temper reachability + echoes whoami.
- `POST /chat` body `{"message": str}` — searches Temper for hits,
  writes the user message as one episode, returns the hits. No LLM
  call yet.

## Quick start

```bash
cd agents/smith
python -m venv .venv && source .venv/bin/activate
pip install -e .

cp .env.example .env
# edit .env: TEMPER_API_KEY=mk_... (create on http://127.0.0.1:18088/admin/integrate)

smith   # or: python -m smith
# -> Smith listens on http://127.0.0.1:18099
```

Smoke test:

```bash
curl http://127.0.0.1:18099/healthz
curl -X POST http://127.0.0.1:18099/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"hi smith, this is the first thing i told you"}'
```

## Layout

```
src/smith/
  __init__.py
  __main__.py        # entrypoint — `smith` / `python -m smith`
  config.py          # pydantic-settings, env-driven
  temper.py          # async Temper API client (write / search / whoami / health)
  mcp.py             # MCP server config parser; client wiring TBD with framework
  agent.py           # PLACEHOLDER tool-use loop; replaced when framework picked
  server.py          # tiny FastAPI: /healthz + /chat
```

## Next decisions

1. **Agent framework** — see `docs/framework-comparison.md`. Replace
   `agent.run_turn()` with the real LLM loop once chosen.
2. **MCP transport** — wire `smith.mcp.open_clients()` against either
   the framework's MCP adapter or the official `mcp` Python SDK.
3. **Identity model** — one API key for the human's personal assistant,
   OR one key per (human, role) so memory is sliced. Today it's one
   key. With agent_slug in Temper, the slug `smith` keeps it isolated.
