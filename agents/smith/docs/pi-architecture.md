# pi-coding-agent — runtime surface Smith builds on

Reference for Smith engineers. Smith is built on top of
`@earendil-works/pi-coding-agent` (currently 0.74). Everything Smith
does is either an **extension** registered against pi's
`ExtensionAPI`, or pure business code that the extension calls into.

This doc is the **as-of-0.74 snapshot** of what pi gives us. If you're
deciding "should I build this in Smith or use a pi facility", check
here first.

Source path:
`agents/smith/node_modules/.pnpm/@earendil-works+pi-coding-agent@.../dist/core/extensions/types.d.ts`

---

## Mental model

```
┌──────────────────────────────────────────────┐
│              pi runtime                      │
│                                              │
│  AgentSession  ─ one chat thread             │
│      ↓                                       │
│  pi.prompt() ─ runs a turn                   │
│      ↓                                       │
│  LLM call loop ─ tools, deltas, thinking     │
│      ↓                                       │
│  events fire at every step                   │
│      ↓                                       │
│  ┌─────────────────────────────────────┐     │
│  │ extensions (factories)              │     │
│  │   - get a single `pi: ExtensionAPI` │     │
│  │   - register tools / events / cmds  │     │
│  │   - persist via appendEntry         │     │
│  └─────────────────────────────────────┘     │
└──────────────────────────────────────────────┘
```

pi handles: session/turn lifecycle, LLM call streaming, tool loop,
compaction, prompt assembly, skill discovery, model registry,
thinking-level wiring, an event bus, custom-message rendering in TUI.

pi does **NOT** handle: MCP connection lifecycle (deliberate —
[author's stance](https://mariozechner.at/posts/2025-11-02-what-if-you-dont-need-mcp/)),
task scheduling, document store / vault, business DB, cross-agent
communication, anything domain-specific.

---

## Extensions

An extension is a factory:

```ts
type ExtensionFactory = (pi: ExtensionAPI) => void | Promise<void>;
```

`ResourceLoader` discovers them (from `.smith/extensions/` and similar
paths) and pi calls each factory once per session start. The factory
sets up everything by interacting with the single `pi` handle.

Smith currently has 4 factories:
- `smith-personality.ts` — system prompt + auto-recall
- `temper-memory.ts` — TEMPER tools
- `mcp-bridge.ts` — MCP server connection + tool wrapping
- (in the new architecture: `plugin-system`, `resource-system`,
  `task-scheduler` replace some of these)

---

## ExtensionAPI surface

### Events (`pi.on(name, handler)`)

Lifecycle (session-level, fire once per state change):
| Event | When |
|---|---|
| `session_start` | session created/resumed/forked. `reason ∈ startup/reload/new/resume/fork` |
| `session_before_switch` | before switching to another session (cancellable) |
| `session_before_fork` | before forking (cancellable) |
| `session_before_compact` | before context compaction (cancellable / customizable) |
| `session_compact` | after compaction |
| `session_shutdown` | clean exit |
| `session_before_tree` / `session_tree` | tree-snapshot operation |
| `resources_discover` | extension can return `{skillPaths, promptPaths, themePaths}` to add resource directories |

Turn-level (fire per turn / message / tool call):
| Event | When |
|---|---|
| `before_agent_start` | **the recall hook Smith uses.** Returns `{systemPrompt?, ...}` — extension can prepend/override the system prompt for this turn |
| `agent_start` | turn began |
| `agent_end` | turn complete (includes full message list) |
| `turn_start` / `turn_end` | inner LLM-call boundaries |
| `message_start` / `message_update` / `message_end` | streaming deltas |
| `tool_execution_start` / `tool_execution_update` / `tool_execution_end` | tool runs |
| `tool_call` | **before the tool runs — the approval gate** (return `{deny: true, reason}` to block) |
| `tool_result` | after tool completes (can transform the result) |
| `before_provider_request` / `after_provider_response` | raw HTTP boundary |
| `model_select` / `thinking_level_select` | user changed settings |
| `input` | user typed input (can transform) |
| `user_bash` | user ran a bash command via UI |

The `tool_call` hook is how Smith's approval gate works
(`approval-store.ts`): for destructive tools, return `{deny}` until
the user clicks Approve, then let the retry through.

### Tool registration

```ts
pi.registerTool<TParams extends TSchema, TDetails, TState>(tool: {
  name: string;                           // unique
  label?: string;                         // UI display
  description: string;                    // shown to LLM
  parameters: TSchema;                    // typebox
  execute(toolCallId, args): Promise<ToolResult>;
  // ...renderer, etc.
});

pi.getAllTools(): ToolInfo[];             // every registered tool
pi.getActiveTools(): string[];            // currently enabled
pi.setActiveTools(names: string[]): void; // change active set (runtime)
```

**Key fact**: `registerTool` is **call-once, name-unique**. You
cannot unregister or re-register a tool with the same name. To
"disable" a tool: drop it from `setActiveTools`. To **change** a
tool's behavior at runtime: have its `execute` close over a mutable
client reference; swap the reference; keep the registered tool.

This shapes the Plugin layer (below): plugins can be enabled/disabled
without restart (setActiveTools); plugin secrets/endpoints can change
without restart (execute closes over a reference that the plugin
manager swaps). But **adding a whole new plugin's tool set requires
a Smith restart** (new tool names → new registerTool calls during
load).

### Commands, shortcuts, flags

```ts
pi.registerCommand(name, { ... })         // /custom-cmd in chat
pi.registerShortcut(KeyId, { handler })   // keyboard
pi.registerFlag(name, { type, default }); pi.getFlag(name);  // CLI
```

Smith uses commands sparsely; flags not at all.

### Provider registration

```ts
pi.registerProvider(name, {
  baseUrl, apiKey, api,                  // OpenAI-compatible URL
  models?: [{ id, name, contextWindow, ... }],
  oauth?: { login, refreshToken, getApiKey },
  streamSimple?: (model, ctx, opts) => stream,
  headers?: Record<string, string>,
});
pi.unregisterProvider(name);
```

Smith already uses this for `forti-k2` (custom OpenAI-compatible
proxy) via `session-manager.ts`. Provider != plugin — providers are
for LLM choice, plugins are for tools/external services.

### Session manipulation

```ts
pi.setSessionName(name)
pi.appendEntry(customType, data)              // persist custom data in session JSONL
pi.setLabel(entryId, label)                   // mark for bookmarking
pi.sendMessage({customType, content, ...}, { triggerTurn, deliverAs })
pi.sendUserMessage(content, { deliverAs })    // inject as if user typed
```

`appendEntry` is how the task scheduler will persist `task_fired`
markers in the session log so the UI can render them inline with
chat. `sendUserMessage(deliverAs: "nextTurn")` is how a scheduler
fires a task without yanking the current turn — the message lands at
the next opportunity.

### Custom message rendering (TUI only)

```ts
pi.registerMessageRenderer<T>(customType, (entry, ctx) => Component);
```

Lets you draw custom UI for `appendEntry`'d data inside pi's TUI
mode. Smith uses its own web UI; this is only relevant if someone
also wants pi's terminal mode.

### Inter-extension communication

```ts
pi.events: EventBus     // emit + on, arbitrary string event names
```

This is the **canonical way Smith extensions talk to each other**.
Plugin layer emits `plugin_event:<slug>`; task scheduler subscribes.
No layer imports another layer directly — pi's bus is the mediator.

### Misc helpers

```ts
pi.exec(cmd, args, options)              // shell, used by git sync etc.
pi.getCommands()                          // list available slash commands
pi.setModel(model)                        // runtime model swap
pi.setThinkingLevel(level)
```

---

## Resource loader

pi has a separate `ResourceLoader` (one tier above `ExtensionAPI`)
that discovers:

- **Extensions** — TS modules to load
- **Skills** — `.md` files (with frontmatter) auto-included in the
  system prompt for the LLM to know about
- **Prompts** — slash-command templates the user can invoke
- **Themes** — TUI color schemes

The `resources_discover` event lets any extension return
`{skillPaths, promptPaths, themePaths}` to push more paths into the
loader. We use this so Smith's vault can register itself as a skill
root (extension files in `.smith/extensions/`) without hardcoding the
search path.

`disable-model-invocation: true` in a skill's frontmatter is a signal
"this file exists but don't auto-load it into system prompt" —
useful for vault content that should be discoverable but not pasted
into every turn.

---

## What we DON'T get from pi (and have to build in Smith)

| Need | Smith's solution |
|---|---|
| MCP connection lifecycle | `plugin-system` extension wraps MCP client + persists registration in SQLite |
| Per-user/per-secret config | SQLite + Fernet encryption in `.data/smith.db` |
| Vault / document store | `resource-system` extension owns `.data/vault/` (git working tree) |
| Persistent task list | SQLite `tasks` table |
| Cron / interval scheduling | In-process `setInterval` driven by `triggers` table |
| Cross-agent coordination (Smith ↔ Forge ↔ ...) | Out of scope for now; each agent is independent |
| HTTP control plane (`/chat`, `/conversations`, etc.) | Hono server in `server.ts` — runs alongside pi, owns its own routes |

---

## Smith's existing extensions, and how they map

| Extension | Role | Status under new architecture |
|---|---|---|
| `smith-personality.ts` | system prompt + auto-recall hook | **Keep**. auto-recall internally migrates from `t.search()` to `ResourceManager.search()` |
| `temper-memory.ts` | memory tools (`memory_*`, `remember`, `correct`, etc.) | **Absorbed**. Becomes a builtin Plugin (temper) + ResourceProvider (temper-episodes + blocks). Same tools exist with same names, but the wiring runs through the new abstractions. |
| `mcp-bridge.ts` | env-based MCP loader | **Replaced**. Logic moves into `plugin-system`'s MCPPlugin; env-driven init becomes one of the migration sources for the SQLite registry. |

---

## When to read what

- "Can I make X happen on every turn?" → check the **events table** above
- "How do I expose Y to the LLM?" → `registerTool`
- "How do I make two layers talk?" → `pi.events`
- "How do I show inline UI for my data?" → `appendEntry` + (web UI custom renderer; pi.registerMessageRenderer is TUI only)
- "I need to change tools at runtime" → `setActiveTools` (enable/disable) or in-execute swap (behavior change); restart needed for new tool names

For anything pi doesn't have — that's `docs/smith-architecture.md`'s
job.
