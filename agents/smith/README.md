# smith — personal company-level agent

Long-running TypeScript process that drives an LLM tool-use loop with
two big external surfaces:

- **TEMPER** for memory (the service in the parent repo, HTTP only).
- **MCP** for everything else — your internal company systems already
  expose MCP servers; Smith bridges them into pi-coding-agent's tool
  surface.

Built on [**pi-coding-agent**](https://github.com/earendil-works/pi) —
the same harness that powers openclaw / harness. Smith is a Temper
*client*; nothing here imports `memory_service`.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Language | TypeScript, ESM, Node 20.6+ | matches pi-coding-agent + openclaw/harness |
| Agent runtime | `@earendil-works/pi-coding-agent` | tool loop, session, compaction, extensions |
| LLM | `@earendil-works/pi-ai` (multi-provider) | one switch from Claude → OpenAI → Ollama |
| Memory | TEMPER over HTTP | one process, one identity |
| Tool transport | MCP via `@modelcontextprotocol/sdk` | the integration story we already have |
| HTTP control plane | Hono + `@hono/node-server` | small, ESM-native, SSE-friendly |
| Tool schemas | TypeBox | what pi expects |

## Layout

```
agents/smith/
├── package.json
├── tsconfig.json
├── .env / .env.example       (.env is gitignored — never committed)
├── src/
│   ├── index.ts              entrypoint — boots Hono on $SMITH_PORT
│   ├── config.ts             dotenv → frozen typed config
│   ├── temper.ts             HTTP client for TEMPER (write / search / health / whoami)
│   ├── server.ts             /healthz + /chat
│   ├── session-manager.ts    per-conversation_id AgentSession pool
│   └── extensions/
│       ├── temper-memory.ts  registers memory_search / memory_write tools
│       └── mcp-bridge.ts     reads MCP_SERVERS, registers one pi tool per MCP tool
└── docs/
    └── framework-comparison.md   how we chose pi-coding-agent
```

## Quick start

```bash
cd agents/smith
npm install
cp .env.example .env
# edit .env — TEMPER_API_KEY, LLM_API_KEY at minimum

npm run dev
# -> http://127.0.0.1:18099
```

Smoke test:

```bash
curl http://127.0.0.1:18099/healthz
# {"status":"ok","temper_user":"you@yourco.com",...}

curl -X POST http://127.0.0.1:18099/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"remember that i prefer postgres for new projects"}'
# {"conversationId":"default","reply":"..."}

curl -X POST http://127.0.0.1:18099/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"what database do i prefer?"}'
# Smith calls memory_search → Temper → paraphrases the hit.
```

## What it does today

- `POST /chat` — single turn. Body `{message, conversationId?}`. Returns the
  assistant's final text. `conversationId` defaults to `"default"`. SSE
  streaming TBD.
- `GET /healthz` — pings Temper + echoes whoami + counts active sessions.
- `memory_search` / `memory_write` tools wired against TEMPER's HTTP API.
- `MCP_SERVERS` env-var driven MCP bridge — declares servers as
  `name=stdio:///path/to/binary` or `name=http(s)://host/mcp`, listed
  tools get registered as `<name>__<tool>` on the pi side.

## What's deferred

- **SSE streaming** on `/chat` (pi's `session.subscribe` already produces
  text_delta events; just need to plumb them to the response body).
- **Conversation persistence** — sessions live in memory and die with the
  process. Switch `SessionManager.inMemory()` → disk JSONL or a
  Temper-backed implementation when continuity-across-restart matters.
- **MCP server args / headers / auth** — MVP only supports stdio with a
  bare path and HTTP without custom headers. Wrap in a shell script if
  you need args; add auth handling as use cases arrive.
- **Web UI / IM bot** — Smith only speaks HTTP today. Wire a thin client
  in front of `/chat` when needed.

## Design notes

- **pi-coding-agent has no built-in MCP.** Author position is "build a
  CLI tool with a README (Skill) instead." That's an OK position for
  *coding* agents but wrong for our enterprise context where MCP is the
  standard internal interface. The `mcp-bridge` extension adapts at the
  edge: list MCP tools at startup, register each via `pi.registerTool`,
  route calls through the MCP `Client` at execute time.

- **Memory routing.** Smith authenticates with one Temper API key. That
  key has an `agent_slug` so writes default to
  `agent:<user_id>/smith`, isolated from the user's other agents. To
  share knowledge with another agent under the same user, write
  explicitly to `namespace: "user:me"`.

- **Why `pi-ai`'s `getModel(provider, model)` and not a string.** pi
  resolves to a `Model` object that carries provider-specific quirks
  (extended thinking, context window, capability flags). The
  `AuthStorage` + `ModelRegistry` pair handles API keys at runtime.
  `config.ts` maps Smith's provider-agnostic `LLM_API_KEY` env into the
  exact env var pi-ai's `env-api-keys.ts` reads for each provider.
