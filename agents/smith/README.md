# smith — personal company-level agent

Long-running TypeScript process that drives an LLM tool-use loop with
two big external surfaces:

- **TEMPER** for memory (the service in the parent repo, HTTP only).
- **MCP** for everything else — your internal company systems already
  expose MCP servers; Smith bridges them into pi-coding-agent's tool
  surface (wired; first real server connect is on the open list).

Built on [**pi-coding-agent**](https://github.com/earendil-works/pi) —
the same harness powering openclaw / harness. Smith is a TEMPER
*client*; nothing here imports `memory_service`.

> **For depth**, see:
> - `docs/design.md` — full design / architecture / contracts / glossary
> - `docs/roadmap.md` — TODO + cross-cutting concerns (security, etc.)
> - `docs/fortinet-mcp-servers.md` — internal MCP servers Smith targets
> - `docs/framework-comparison.md` — how we picked pi-coding-agent

## Stack

| Layer | Choice |
|---|---|
| Language | TypeScript, ESM, Node 20.6+ |
| Agent runtime | `@earendil-works/pi-coding-agent` |
| LLM | `@earendil-works/pi-ai` (multi-provider; corp gateways via `registerProvider`) |
| Memory | TEMPER over HTTP |
| Tool transport | MCP via `@modelcontextprotocol/sdk` |
| HTTP control plane | Hono + `@hono/node-server` |
| Tool schemas | TypeBox (pi requirement) |
| Markdown rendering | `marked` (inlined into UI HTML, no CDN) |

## Quick start

```bash
cd agents/smith
pnpm install
cp .env.example .env
# Edit .env. At minimum:
#   TEMPER_API_KEY=mk_…       (create on http://127.0.0.1:18088/admin/integrate)
#   LLM_PROVIDER=<provider>   (anthropic | openai | deepseek | google | <custom>)
#   LLM_API_KEY=<key>
#   LLM_MODEL=<id>
# For a corporate OpenAI-compat gateway also set:
#   LLM_BASE_URL=http://nac-ai.internal.example/v1
# Optional bearer auth (REQUIRED if SMITH_HOST is not 127.0.0.1):
#   SMITH_SECRET=<choose-something>

pnpm dev
# -> http://127.0.0.1:18099
```

Open the chat UI:

- No auth (default): http://127.0.0.1:18099/
- With `SMITH_SECRET=hunter2`: http://127.0.0.1:18099/#secret=hunter2
  (the page reads the hash, persists to sessionStorage, scrubs the URL)

## Layout

```
agents/smith/
├── package.json
├── tsconfig.json
├── .env / .env.example          .env is gitignored
├── README.md                    (this file)
├── docs/
│   ├── design.md
│   ├── roadmap.md
│   ├── fortinet-mcp-servers.md
│   └── framework-comparison.md
├── .smith/                      bundled with the repo
│   ├── skills/*.md              LLM-loaded on demand (frontmatter required)
│   └── prompts/*.md             /name slash commands
├── .data/                       runtime state, gitignored
│   ├── smith-sessions/<id>.jsonl     per-conversation pi history
│   ├── smith-sessions/_index.json    conversation manifest for the picker
│   └── audit.log                     destructive tool calls
└── src/
    ├── index.ts                 entrypoint, banner, SIGINT cleanup
    ├── config.ts                dotenv → frozen typed SmithConfig
    ├── server.ts                Hono app + inline chat UI + SSE
    ├── temper.ts                HTTP client for TEMPER
    ├── session-manager.ts       per-conversation_id AgentSession pool
    ├── conversation-index.ts    JSON manifest, atomic writes
    ├── approval-store.ts        gate state + isDangerous heuristic
    └── extensions/
        ├── smith-personality.ts    system prompt + auto-recall (per-turn)
        ├── temper-memory.ts        memory_search / memory_write tools
        ├── mcp-bridge.ts           connect MCP_SERVERS, register tools
        ├── approval-gate.ts        tool_call hook + audit log
        └── compaction-policy.ts    preserve identity facts; archive summary to TEMPER
```

## What it does today

### Chat UI (`GET /`)

- Markdown-rendered replies (code blocks, tables, lists, blockquotes,
  inline code)
- Streaming token-by-token via SSE
- Tool calls surface as inline pill badges that flip from ↻ to ✓/✗
- Reasoning models' thinking blocks render as a collapsed `<details>`
- Header dropdown to switch between past conversations
- 🗑 to delete the current conversation (optional archive to TEMPER first)
- "New conversation" rotates the conversationId

### HTTP API

| Method | Path | Returns |
|---|---|---|
| `GET` | `/` | Chat UI |
| `GET` | `/healthz` | `{status, temper_user, llm_provider, llm_model, active_sessions, ...}` |
| `POST` | `/chat` | SSE if `Accept: text/event-stream`, else `{reply, stopReason}` JSON |
| `POST` | `/approve` | Approve a pending destructive tool call |
| `POST` | `/deny` | Cancel a pending destructive tool call |
| `GET` | `/pending/:conversationId` | Current pending approval (UI re-fetch hook) |
| `GET` | `/conversations` | Newest-first conversation index |
| `DELETE` | `/conversations/:id?archive=true` | Delete JSONL + drop entry; optional summary to TEMPER first |

All routes except `/` and `/healthz` are gated by `SMITH_SECRET` when set.

SSE event types on `/chat`: `delta`, `thinking`, `tool_start`, `tool_end`,
`tool_pending`, `error`, `done`.

### Memory (two layers)

1. **Conversation history** — pi's native JSONL at
   `.data/smith-sessions/<conversationId>.jsonl`. Restart resumes the
   thread.
2. **Long-term semantic memory** — TEMPER over HTTP. Per-agent
   namespace `agent:me/<SMITH_AGENT_SLUG>`. Auto-recall runs every
   turn (search + raw episode cross-check) against this scope only —
   `user:me` is shared cross-agent and DOES NOT bleed in by default.
   LLM can call `memory_search` explicitly with `namespaces=["user:me"]`
   to reach it.

### Tools

- `memory_search(query, limit?, as_of?, namespaces?)` — TEMPER search.
- `memory_write(content, source_description?, reference_time?, tags?, saga?, namespace?)`
  — write one episode.
- `<server>__<tool>` — MCP-bridged. Names from each connected server's
  `listTools()`.

### Destructive tool gate

Tool names matching mutation verbs (`*_close`, `*_merge`, `*_delete`,
`*_send`, `*_update`, `*_create`, …) block on first call and emit
`tool_pending` SSE. UI shows Approve / Deny buttons. On Approve, smith
records (convId, toolName, argsHash) → LLM retries next turn → hook
consumes the approval → tool runs. Audit log at `.data/audit.log`.

### Skills & Prompt Templates

Drop `.md` files with YAML frontmatter into `.smith/skills/` or
`.smith/prompts/`. pi auto-discovers via `additionalSkillPaths` /
`additionalPromptTemplatePaths`. Two seed files ship in the repo:

```yaml
---
name: short-stable-id
description: |
  Sentence that tells the LLM when to load this. Concrete beats vague.
---
```

Skills load on-demand when the LLM thinks the description fits the
turn; Prompt Templates expand when the user types `/<name>`.

Real team-curated content will ship as a separate npm package
(`@fortinet/smith-skills`, see `docs/roadmap.md` §B6) so multiple
people can subscribe to one canonical set.

### Compaction

When pi auto-compacts old turns into a summary, Smith does two things:

1. Injects preservation rules into the summary LLM call (keep
   `memory_write` content verbatim, keep tool decisions, keep
   identity facts).
2. Writes the resulting summary as one Temper episode tagged
   `compaction-summary`, so cross-thread recall can still find what
   happened in turns 1–50 of yesterday's conversation.

## Configuration (env vars)

See `.env.example` for inline docs; full table in `docs/design.md` §8.

| Var | Required | Default | Notes |
|---|---|---|---|
| `TEMPER_BASE_URL` | y | `http://127.0.0.1:18088` | |
| `TEMPER_API_KEY` | y | — | Determines Smith's `agent_slug` server-side |
| `SMITH_AGENT_SLUG` | n | `smith` | Must match the key's slug |
| `LLM_PROVIDER` | y | — | pi-ai provider name (custom OK if `LLM_BASE_URL` set) |
| `LLM_API_KEY` | y | — | |
| `LLM_MODEL` | y | — | Model id |
| `LLM_BASE_URL` | n | — | Set to enable custom OpenAI-compat registration |
| `MCP_SERVERS` | n | empty | `name=stdio:///path` or `name=http(s)://…`, comma-separated |
| `SMITH_HOST` | n | `127.0.0.1` | Non-loopback REQUIRES `SMITH_SECRET` |
| `SMITH_PORT` | n | `18099` | |
| `SMITH_SECRET` | n | empty | Bearer for `/chat`, `/approve`, `/deny`, `/pending`, `/conversations` |

## Design notes

- **pi-coding-agent has no built-in MCP** — author's deliberate
  stance. Smith's `mcp-bridge` extension adapts at the edge: list MCP
  tools at startup, register each via `pi.registerTool`, route calls
  through the MCP `Client` at execute time. See
  `docs/fortinet-mcp-servers.md`.

- **Memory routing.** Smith authenticates with one TEMPER API key
  whose `agent_slug` determines the per-agent namespace. Writes
  default to `agent:<user_id>/<slug>`, isolated from the user's other
  agents. To share knowledge, write explicitly with
  `namespace: "user:me"`.

- **System prompt is non-optional.** pi has no `createAgentSession.systemPrompt`
  option, so Smith injects via the `before_agent_start` event. Same
  hook also runs the per-turn auto-recall — searching the agent's
  scope for the user's message and pasting hits + source episodes
  into the prompt as ground truth. Without this, the model often
  forgets to call `memory_search` itself.

- **Conversations persist as JSONL.** `SessionManager.open(path)` —
  pi handles the format, atomic writes, re-load on resume. Smith
  maintains a `_index.json` manifest on top so the UI can show a
  picker.

- **Why we don't trust `getModel`'s typed catalog.** pi-ai's
  `getModel<TProvider, TModelId>` is generic over its compile-time
  MODELS table, which can't satisfy arbitrary env strings. We use
  `ModelRegistry.find()` instead, and call `registerProvider` first
  if `LLM_BASE_URL` is set (custom OpenAI-compat gateways).

## Open items

| Area | Status |
|---|---|
| MCP `npx -y <pkg>` launch | bridge supports `stdio:///path` today; needs cmd+args |
| First-call MCP SSO push pre-warm | TBD |
| Fortinet SSO for LLM_API_KEY (OAuth) | pi has the hook; needs SSO spec |
| Conversation auto-summary on session_shutdown | manual `?archive=true` available |
| Scheduled tasks (`/standup` every morning) | non-goal for MVP |
| Multi-tenant / multi-user deploy | single-tenant by design |

Full list in `docs/roadmap.md`. Security/data-hygiene checklist
(CC1–CC9) in `docs/roadmap.md` "Cross-cutting" section.
