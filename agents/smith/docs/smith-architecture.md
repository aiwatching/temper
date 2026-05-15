# Smith — business architecture on top of pi

Smith is one user's personal AI agent. It runs as its own Node process
per user. Conceptually it's three business-layer subsystems sitting on
top of the lean pi-coding-agent runtime (see `pi-architecture.md`):

```
                            Smith chat / brief / admin UI
                                       │
   ┌───────────────────────────────────┴───────────────────────────────┐
   │                                                                   │
   │  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐       │
   │  │ Plugin         │  │ Resource       │  │ Task           │       │
   │  │ subsystem      │  │ subsystem      │  │ subsystem      │       │
   │  │                │  │                │  │                │       │
   │  │ MCP / HTTP /   │  │ Vault (git) +  │  │ Cron +         │       │
   │  │ shell adapters │  │ TEMPER         │  │ interval +     │       │
   │  │ + secrets +    │  │ providers      │  │ plugin_event   │       │
   │  │ enable/disable │  │                │  │ triggers       │       │
   │  └────────┬───────┘  └────────┬───────┘  └────────┬───────┘       │
   │           │                   │                   │               │
   │           └───────── pi.events EventBus ──────────┘               │
   │                              │                                    │
   │                       ┌──────┴──────┐                             │
   │                       │  pi runtime │   ← agent session, tool     │
   │                       │             │     loop, LLM streaming     │
   │                       └─────────────┘                             │
   └───────────────────────────────────────────────────────────────────┘
```

Each subsystem is one pi extension (factory + the `pi: ExtensionAPI`
handle). Code in each subsystem talks **only** to pi and to its own
storage — never directly to another subsystem. Cross-subsystem
coordination goes through `pi.events`.

This doc covers WHAT each subsystem does, WHERE it persists state,
and HOW it maps to pi's primitives. For pi's surface, see
`pi-architecture.md`.

---

## Cross-cutting

### Storage

```
agents/smith/
├── .smith/                 (npm/git-distributed, optional per project)
│   ├── extensions/         pi auto-discovers via resource_loader
│   ├── skills/             ditto
│   ├── prompts/            ditto
│   └── briefs/             (existing)
└── .data/                  (per-user runtime state, never in git)
    ├── smith.db            SQLite — plugins / tasks / triggers / secrets
    ├── smith-sessions/     pi's per-conversation JSONL (existing)
    ├── vault/              git working tree — the markdown vault
    │   ├── .git/
    │   ├── meetings/, people/, projects/, ...
    │   └── .smith/vault.toml
    ├── audit.log
    └── recall/             optional dumps (SMITH_RECALL_LOG=dump)
```

One SQLite file owns three tables (`plugins`, `tasks`, `triggers`,
`secrets`). Vault is a separate working directory. No service has its
own DB — everything per-Smith-process state lives in `.data/`.

### Cross-subsystem communication: pi.events

```
plugin-system           emit("plugin_event:mantis",
                             { kind: "new_bug", id: 12345 })
                              │
                              ▼
task-scheduler          on("plugin_event:mantis")
                          → find triggers WHERE kind='plugin_event'
                                            AND filter matches
                          → instantiate task → write tasks table
                          → pi.sendUserMessage(
                              "📋 New task fired: triage #12345",
                              { deliverAs: "nextTurn" })
                              │
                              ▼
agent (next turn)       sees the user message → auto-recall →
                          calls list_my_tasks / update_task /
                          plugin tools / ...
```

Pi's EventBus is the **only** allowed inter-subsystem call. Subsystems
don't import each other; everything is event-mediated. Keeps each
subsystem testable in isolation.

### Approval gate (existing pattern — kept)

Destructive tools fire `pi.on("tool_call", ...)`; `approval-store.ts`
returns `{deny}` until the user clicks Approve in the UI. This works
regardless of which subsystem owns the tool. Plugin tools (mantis
close_bug), Resource tools (`resource_write` to vault), Task tools
(`update_task` to abandoned) all go through the same gate.

---

## 1. Plugin subsystem

**Purpose**: connect external services (MCP servers, HTTP APIs, shell
commands) as tools the LLM can call.

### What "plugin" means here

Anything that produces a set of LLM-callable tools and needs a
persisted configuration + (often) a secret. Today three kinds:

| Kind | Example | Adapter |
|---|---|---|
| `mcp` | mantis, gitlab, sharepoint | Wraps MCP SDK client (stdio/http/sse), translates `tools/list` and `tools/call` |
| `http` | A REST API with an OpenAPI spec | Reads OpenAPI, generates a typebox tool per operation, executes via fetch |
| `shell` | Local CLI like `kubectl` / `git` shortcuts | Pre-configured command set; each tool is a templated shell invocation |

New kinds are an interface implementation away — `Plugin.invoke()` is
the contract.

### Data

```sql
CREATE TABLE plugins (
  slug          TEXT PRIMARY KEY,        -- 'mantis', 'jira-fortinet', ...
  kind          TEXT NOT NULL,           -- 'mcp' | 'http' | 'shell'
  display_name  TEXT NOT NULL,
  config_json   TEXT NOT NULL,           -- shape depends on kind:
                                         --   mcp:   {transport, endpoint, args, auth: {type, header}}
                                         --   http:  {base_url, openapi_url, headers}
                                         --   shell: {commands: [{name, template}]}
  secret_ref    TEXT,                    -- foreign-key into secrets table (if needed)
  enabled       INTEGER NOT NULL DEFAULT 1,
  last_seen_at  TEXT,                    -- last successful health check
  last_tool_count INTEGER,
  last_error    TEXT,                    -- last failure detail
  created_at, updated_at
);

CREATE TABLE secrets (
  ref           TEXT PRIMARY KEY,        -- 'plugin/mantis/apikey', generated
  ciphertext    BLOB NOT NULL,           -- Fernet-encrypted, key in env
  created_at, updated_at
);
```

Encryption: Fernet (`cryptography` JS port) with the key in
`SMITH_SECRET_KEY` env var. First boot without the var → Smith
generates one, writes it to `.env`, logs a warning to back it up.

### pi interfaces used

| pi call | Use |
|---|---|
| `pi.registerTool` | Called once per plugin tool at startup, with execute closing over the plugin's mutable client reference |
| `pi.setActiveTools(names)` | Plugin enable/disable: rebuild the active list from `WHERE enabled=1` plugins |
| `pi.on("tool_call")` | Routes destructive plugin tools through the approval gate (already in `approval-store.ts`) |
| `pi.events.emit("plugin_event:<slug>", payload)` | When a plugin observes an external event (poll, webhook, MCP notification) it broadcasts so the task scheduler can react |
| `pi.exec(...)` | Used by ShellPlugin |

### Constraint: tool set is fixed at startup

pi has no `unregisterTool`. So:
- **Adding/removing a plugin entirely** → requires Smith restart (the
  new tool names need a fresh `registerTool` pass during extension
  load).
- **Toggling a plugin's enabled flag** → no restart; uses
  `setActiveTools` to remove its tools from the active set.
- **Rotating a secret or changing endpoint** → no restart; the
  execute closure reads the live client reference, and the plugin
  manager swaps the reference on next poll.

Confirmed acceptable for MVP; revisit if pi exposes
`unregisterTool` in a later version.

### Connection lifecycle

```
PluginManager (singleton, per Smith process)
  ├─ map: slug → Plugin instance
  ├─ poll(): every 30s → re-read plugins table → diff
  │         ├─ new rows → don't add tools (restart needed) but warn
  │         ├─ removed rows → drop the plugin instance, remove from
  │         │                  active tools, dispose client
  │         ├─ config/secret changed → reconnect underlying client,
  │         │                          swap reference (tool keeps working)
  │         └─ enabled changed → adjust active tool set
  └─ health(): test connection per plugin, write last_seen / last_error
```

### HTTP API (Smith's own server, NOT TEMPER)

Routes added to `agents/smith/src/server.ts`:

```
GET    /plugins                          — list + health snapshot
POST   /plugins                          — create (body has plaintext secret;
                                            server immediately encrypts)
PUT    /plugins/:slug                    — update non-secret fields + enabled
PUT    /plugins/:slug/secret             — rotate secret
POST   /plugins/:slug/test               — force a ping, update last_seen
DELETE /plugins/:slug
```

### UI

Smith's existing React UI (`src/web/`) gains a `/plugins` page. Same
visual language as `/briefs` and `/admin/blocks`. Table per kind,
"Add plugin" wizard that branches on kind, secret-rotate flow.

### Migration

`.env`'s legacy `MCP_SERVERS=name=URL,...` is a one-time import path:
on first Smith startup after the upgrade, if `plugins` is empty and
`MCP_SERVERS` is set, parse it and insert each entry as an `mcp`-kind
plugin. Then it can be removed from `.env`.

---

## 2. Resource subsystem

**Purpose**: give the agent a unified way to read, write, search, and
list "knowledge" regardless of where it actually lives. Today three
backends:

| Provider | Backed by | URI scheme | Best for |
|---|---|---|---|
| `vault` | local git working tree at `.data/vault/` | `vault://<path>` | Long-form deliberate documents (meeting notes, person dossiers, saga summaries). Human-edited via any markdown editor; agent reads/writes via tools. |
| `temper-episodes` | TEMPER `/v1/episodes` + `/v1/search` | `temper://episodes/<uuid>` | Third-party facts, time-aware events, anything the user said about people/projects/the world |
| `blocks` | TEMPER `/v1/memory/blocks` | `blocks://<key>` | First-person assertions (nicknames, preferences, current focus). Pinned blocks land in every system prompt. |

Future providers (deferred): Confluence, SharePoint, S3, internal
Wiki — adding one is implementing the `ResourceProvider` interface.

### Decision rule (the same one in `docs/memory-blocks.md`, extended)

| User's statement | Provider | URI |
|---|---|---|
| "Note: meeting today with Sarah, agreed on plan X" | vault | `vault://meetings/2026-05-15-sarah.md` |
| "Call me Heizai" | blocks | `blocks://preferences.nickname_for_assistant` |
| "Sarah teaches Portuguese" | temper-episodes (write) | `temper://episodes/<new-uuid>` |
| "What did Sarah say last week?" | temper-episodes (search) | search → ranked URIs |
| "Pull up the wad-saga writeup" | vault (search) | `vault://sagas/wad-ssl-crash.md` |

The model picks the provider via the URI scheme it passes to
`resource_*` tools. The system prompt teaches the rule.

### Unified tools

```
resource_search(query, scopes?: ["vault", "temper-episodes", "blocks"], limit?)
  → [{ uri, snippet, score, kind }, ...]   federated across providers

resource_read(uri)
  → { uri, content, metadata }              dispatches by scheme

resource_write(uri, content, mode: "create" | "replace" | "append")
  → { uri, ... }                             dispatches by scheme

resource_list(uri_prefix)
  → [{ uri, name, kind, updated_at }, ...]   list under a prefix
```

Old tools (`memory_search`, `memory_write`, `vault_*`, `remember`,
`forget`, `get_memory`) — they continue to exist as aliases that map
to the new dispatcher. **Decision deferred: keep aliases vs deprecate.**
Look at it when implementation gets there (~3 months observation).

### Vault sync model

Vault is a git working tree at `.data/vault/`. Sync is
agent-triggered with debouncing:

```
agent calls resource_write(vault://...)
   → file writes to disk
   → schedule _maybe_sync() in 60s (debounce: each write resets timer)
                  │
                  ▼
   git add . && git commit -m "smith: <auto-summary>"
   git pull --rebase --autostash
   ┌──────────────┴──────────┐
   no conflict          merge conflict
        ▼                    ▼
   git push      vault enters "conflict" state
                 - UI shows red banner with the conflicted files
                 - agent's writes go to inbox/conflict-<ts>.md
                 - user resolves manually in their editor
                 - user clicks "Resolved" in UI → resumes
```

Remote configured in `.data/vault/.smith/vault.toml` (or absent =
local-only):

```toml
[remote]
url = "git@gitlab.fortinet-us.com:zliu/smith-vault.git"
branch = "main"

[sync]
debounce_seconds = 60
auto_pull_interval_seconds = 300       # poll remote for human edits
```

Auth = SSH key in the user's environment (standard git setup); we
don't manage credentials here.

### pi interfaces used

| pi call | Use |
|---|---|
| `pi.registerTool` | The 4 `resource_*` tools (+ alias tools for backward compat) |
| `pi.on("resources_discover")` | Return vault dir → pi can surface vault SKILL.md files as discoverable skills (`disable-model-invocation: true` to avoid auto-injection) |
| `pi.on("before_agent_start")` | Pinned blocks injection (existing) moves into resource subsystem; same mechanism but cleaner ownership |
| `pi.exec` | Spawn `git` for sync (alternative: child_process directly for finer control) |

### Storage

- Vault: filesystem + git
- Provider registry: in-code (just instantiate VaultProvider, TemperEpisodeProvider, TemperBlockProvider; no DB)
- Per-session cache (e.g. last 30s of pinned blocks): in-memory Map

---

## 3. Task subsystem

**Purpose**: scheduled / triggered work. Not just a todo list — has
explicit triggers (cron, interval, plugin_event) so it can do things
like "every Monday at 8am generate a standup".

### Data

```sql
CREATE TABLE tasks (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  body        TEXT,                       -- markdown; can include
                                          -- vault://... / blocks://... links
  state       TEXT NOT NULL,              -- pending | in_progress | needs_review
                                          -- | done | failed | abandoned
  priority    INTEGER NOT NULL DEFAULT 0,
  source      TEXT NOT NULL,              -- 'user' | 'agent' | 'plugin:<slug>'
                                          -- | 'trigger:<id>'
  parent_id   TEXT,                       -- subtask chain
  metadata    TEXT,                       -- JSON: external refs, handoff IDs
  due_at      TEXT,
  created_at, updated_at, started_at, completed_at
);

CREATE TABLE triggers (
  id             TEXT PRIMARY KEY,
  kind           TEXT NOT NULL,           -- 'cron' | 'interval' | 'plugin_event' | 'manual'
  config_json    TEXT NOT NULL,           -- {expression: "0 8 * * MON"} or
                                          -- {seconds: 3600} or
                                          -- {plugin: "mantis", event: "new_bug",
                                          --  filter: {assignee: "me"}}
  task_template  TEXT NOT NULL,           -- {title, body, priority, ...}
                                          -- mustache-rendered at fire time
  enabled        INTEGER NOT NULL DEFAULT 1,
  last_fired_at  TEXT,
  next_fire_at   TEXT,                    -- cron/interval only
  created_at, updated_at
);

CREATE TABLE task_runs (
  id          TEXT PRIMARY KEY,
  task_id     TEXT NOT NULL,
  trigger_id  TEXT,                       -- null = manually created
  state       TEXT,
  log         TEXT,                       -- agent stdout / errors
  started_at, ended_at
);
```

### Trigger kinds

| Kind | Example | Engine |
|---|---|---|
| `cron` | "every Monday 8am generate standup" | `setInterval` driver checks `next_fire_at <= now` every second |
| `interval` | "every hour refresh mantis bug list" | same engine |
| `plugin_event` | "mantis fired new_bug → make a task" | `pi.events.on("plugin_event:*", ...)` |
| `manual` | LLM tool `create_task` directly | no trigger; created on demand |

### pi interfaces used

| pi call | Use |
|---|---|
| `pi.registerTool` | `create_task`, `update_task`, `list_my_tasks`, `schedule_task`, `list_schedules`, `disable_schedule`, `delete_schedule` |
| `pi.events.on("plugin_event:*")` | Plugin → task-scheduler bridge |
| `pi.sendUserMessage(content, { deliverAs: "nextTurn" })` | When a trigger fires, inject the task as a user message so the next turn sees and acts on it |
| `pi.appendEntry("task_fired", task)` | Persist a session-log marker so the UI can render it inline with chat |

### Scheduler engine

```
TaskScheduler
  ├─ in-memory: nothing (every fire goes through SQLite for crash safety)
  ├─ start(): setInterval every 1s — scan triggers WHERE next_fire_at <= now
  │           AND enabled=1
  ├─ fire(triggerId):
  │     - render task_template (mustache with {{now}}, {{plugin_event}})
  │     - INSERT task
  │     - update last_fired_at, compute new next_fire_at
  │     - pi.sendUserMessage("...new task...", { deliverAs: "nextTurn" })
  │     - pi.events.emit("task:fired", task)
  └─ on restart:
     - scan triggers
     - if next_fire_at < now - 1h → DON'T backfill (avoid restart-storm
       of accumulated fires)
     - if (now - 1h) < next_fire_at < now → fire once each
```

### Task execution model

Tasks don't auto-execute. Three paths:

1. **Human executes** — user sees task in `/tasks` board, does it,
   marks done.
2. **Agent executes** — task body has natural-language instructions
   (`@smith pull this week's mantis tickets and summarize`). On the
   next turn the agent (which got the `sendUserMessage` injection)
   decides to call tools, possibly updates the task state. A5
   approval gate still applies for destructive sub-actions.
3. **Handoff to another agent** — task carries
   `metadata.assigned_to: "forge"` (or similar). Smith doesn't try
   to execute; it's a marker. How the handoff actually happens is
   the "Cross-agent coordination" question below — currently
   undefined.

### HTTP API + UI

```
GET    /tasks?state=&source=&limit=
POST   /tasks                              — manual create
PUT    /tasks/:id                          — update state / fields
DELETE /tasks/:id

GET    /triggers
POST   /triggers
PUT    /triggers/:id                       — enable/disable / change config
DELETE /triggers/:id
```

UI page `/tasks`: Kanban board (pending / in_progress / needs_review
/ done). Sidebar lists active `/schedules`. Click a task → expand
body, see linked vault files, change state.

---

## Cross-agent coordination — out of scope here

The original requirement mentioned "code goes to Forge or another
agent". Re-framed correctly: Forge is a **peer agent** built on pi
(like Smith), not a Smith plugin or extension.

Three ways Smith could interact with a peer agent — pick when there's
a real second agent to talk to:

1. **Share TEMPER memory only** — both Smith and Forge auth to TEMPER
   as the same user; episodes / blocks the user writes are visible to
   both. The user manually switches between chat UIs. No code in
   Smith. This is what already works today.

2. **Smith creates tasks with `metadata.assigned_to: "forge"`** —
   tasks sit in Smith's table; some external mechanism (a Forge
   webhook, a Forge cron, or a shared TEMPER query) tells Forge
   "you have new work". Forge updates back via its own write to a
   shared store.

3. **Direct RPC** — Smith POSTs to Forge's HTTP control plane.
   Requires Forge to expose one and Smith to know the URL +
   credentials. Schema-shaped contract between the two.

**Today**: option 1 only (TEMPER as the shared substrate). When
Forge concretely exists with a known interface, we revisit.

---

## File layout (after the refactor)

```
agents/smith/src/
├── index.ts                           bootstrap — register the 3+1 extensions
├── config.ts                          (existing)
├── server.ts                          (existing) + new routes for /plugins, /tasks
├── temper.ts                          (existing) TS client
├── approval-store.ts                  (existing)
├── conversation-index.ts              (existing)
├── session-manager.ts                 (existing)
├── scheduler.ts                       (existing — consolidate runner; keep)
│
├── db/
│   ├── sqlite.ts                      better-sqlite3 wrapper
│   ├── migrations.ts                  forward-only schema migrations
│   └── secrets.ts                     Fernet encrypt/decrypt
│
├── extensions/
│   ├── smith-personality.ts           (existing) system prompt + recall hook
│   ├── plugin-system.ts               NEW — Plugin Manager, MCP/HTTP/Shell adapters
│   ├── resource-system.ts             NEW — ResourceManager, 3 providers, vault sync
│   └── task-scheduler.ts              NEW — task + trigger engine
│
└── web/
    ├── (existing pages)
    ├── plugins.jsx                    NEW
    ├── resources.jsx                  NEW (replaces today's blocks/vault scatter)
    └── tasks.jsx                      NEW
```

Removed/absorbed:
- `extensions/temper-memory.ts` → its tools live in `resource-system`
  (memory_*) and a builtin "temper" Plugin
- `extensions/mcp-bridge.ts` → its logic moves into MCPPlugin under
  `plugin-system`

---

## Build order

| Phase | Subsystem | Depends on | Estimate |
|---|---|---|---|
| **P1** | DB foundation: SQLite + migrations + secrets helper | — | 0.5d |
| **P2** | Plugin: schema + Plugin interface + MCPPlugin (port from mcp-bridge) | P1 | 1d |
| **P3** | Plugin: HTTP API + admin UI | P2 | 1d |
| **P4** | Plugin: pluginManager poll + setActiveTools wiring + .env migration | P2 | 0.5d |
| **P5** | Resource: ResourceProvider interface + 3 providers (vault, temper-ep, blocks) | — | 1d |
| **P6** | Resource: `resource_*` 4 tools + alias old tools to new dispatcher | P5 | 0.5d |
| **P7** | Resource: vault sync engine (debounce + git + conflict state) | P5 | 1d |
| **P8** | Resource: UI page (browse vault tree, edit raw markdown, blocks list) | P7 | 0.5d |
| **P9** | Task: schema + scheduler engine (cron + interval) | P1 | 1d |
| **P10** | Task: plugin_event triggers via pi.events.on | P2, P9 | 0.5d |
| **P11** | Task: LLM tools + HTTP API + Kanban UI | P9 | 1d |

**Total MVP ≈ 8 dev-days**. Order can parallelize (P5-P8 don't depend
on P2-P4), but P1 unblocks everything.

---

## Open questions to resolve as we go

- **resource_* alias strategy** — keep memory_*/vault_*/remember as
  aliases for compat, or deprecate immediately? Decide at P6
  implementation (after observing how the old tools are used in
  existing prompts).

- **Plugin hot-add** — currently requires restart due to pi's no-
  unregister limitation. Watch pi 0.75+ for `unregisterTool`; if it
  lands, plugin add/remove becomes hot-reloadable.

- **plugin_event delivery** — does the plugin manager poll or push?
  MCP servers support `notifications/...` (push); HTTP plugins need
  polling. Implement push-when-available; per-plugin config decides
  polling frequency for the rest.

- **Vault search engine** — start with `ripgrep` shell-out. Switch
  to SQLite FTS when vault > a few thousand files. Embedding-based
  search (matching what TEMPER does for episodes) probably overkill
  for a personal vault.

- **Task body templating** — mustache is fine for MVP. If users want
  Jinja-style logic, revisit.

---

## Why this shape (vs alternatives we considered)

| Alternative | Why not |
|---|---|
| One big monolithic extension | Three concerns with different lifecycles (network reconnects, file watching, scheduler ticks) — separation aids testing + crash isolation |
| Put tasks/plugins in TEMPER | TEMPER is the multi-tenant memory service; Smith is per-user agent config. Putting Smith's runtime config in TEMPER conflates layers. Each Smith process owns its own config. |
| Skip the abstraction layer (just MCP, not Plugin) | Confirmed by user that HTTP-style external services need to plug in too. Doing the interface day-1 costs one day; doing it later costs a refactor. |
| Skip the resource abstraction (keep memory_*/vault_* separate) | Three storage backends with three tool families = LLM has to learn three different "decide where to write" rules. Unified `resource_*` collapses that into URI-scheme dispatch. |
| Use cron daemon / systemd timers for scheduling | Smith is per-user, often per-laptop. Owning the scheduler in-process avoids any OS dependency and survives restart via SQLite. |
