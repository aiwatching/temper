# memory_blocks — structured KV memory

Storage for **first-person assertions about the user** — nicknames,
preferences, current focus, daily routine, bookmarks. Coexists with
the graph (episodes + entities + facts); is **not** a replacement.

Added 2026-05-15 after Smith repeatedly failed to remember things
the user directly told it. See "Why this exists" below for the
decision history and the Graphiti failure modes that forced it.

---

## API surface

Five endpoints under `/v1/memory/blocks`. Every operation is scoped
to the calling user; the API key's `agent_slug` picks the default
"own" scope.

| Method | Path | Body / query | Use |
|---|---|---|---|
| GET | `/v1/memory/blocks` | `?scope=own\|global\|both&pinned=&prefix=` | List, with optional filters |
| GET | `/v1/memory/blocks/<key>` | `?scope=own\|global` | Fetch one (own falls back to global if not found) |
| PUT | `/v1/memory/blocks/<key>` | `{value, pinned?, priority?, description?, scope?}` | Upsert; value REPLACES existing |
| PATCH | `/v1/memory/blocks/<key>` | same shape | `value` is deep-merged (JSONB) |
| DELETE | `/v1/memory/blocks/<key>` | `?scope=own\|global` | Hard delete |

### Schema

```sql
CREATE TABLE memory_blocks (
  id          UUID PRIMARY KEY,
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  agent_slug  TEXT NOT NULL DEFAULT '*',   -- '*' = global
  block_key   TEXT NOT NULL,
  block_value JSONB NOT NULL,
  pinned      BOOLEAN NOT NULL DEFAULT false,
  priority    INTEGER NOT NULL DEFAULT 0,
  description TEXT,
  created_at  TIMESTAMP NOT NULL,
  updated_at  TIMESTAMP NOT NULL,
  updated_by  TEXT,
  UNIQUE (user_id, agent_slug, block_key)
);
```

`agent_slug` is `NOT NULL` with a sentinel `'*'` so the UNIQUE
constraint works (Postgres treats `NULL != NULL`, which would
otherwise let multiple global rows slip past per key).

### Scopes

| Scope | Meaning |
|---|---|
| `own` | The caller's API-key `agent_slug`. If the key has no `agent_slug`, "own" collapses to `'*'`. |
| `global` | The `'*'` sentinel. Visible to every agent under this user. Good for cross-agent identity facts like the user's name. |
| `both` (list only) | Union with shadowing: same key in own and global → own wins. |

### Pinned blocks

`pinned=true` is the signal "auto-inject this into the agent's system
prompt every turn". Smith's `before_agent_start` hook calls
`/v1/memory/blocks?pinned=true&scope=both` and formats the result
into the system prompt before each LLM call. Costs prompt tokens on
every turn — use sparingly.

`priority` orders pinned blocks (higher first) when there are several.

### Key conventions

Not enforced; consistent prefixes help operators reason about content.

| Prefix | Use |
|---|---|
| `preferences.X` | likes / dislikes / nickname / language / theme |
| `persona.X` | identity facts (name, role, location); usually `scope=global` |
| `state.X` | current working context (focus, last topic, in-flight task) |
| `routine.X` | recurring patterns (schedule, oncall, weekly habits) |
| `bookmark.X` | external URLs / IDs |
| `note.X` | free-form user notes |

---

## Smith integration

Smith exposes four LLM tools that wrap the API:

| Tool | When the model uses it |
|---|---|
| `remember(key, value, pinned?, description?, scope?)` | User asserts a durable preference / identity / state |
| `update_memory(key, patch)` | Incremental change to an existing object-valued block |
| `forget(key, scope?)` | User explicitly asks to remove |
| `get_memory(key, scope?)` | Non-pinned block lookup on demand |

`before_agent_start` fetches all pinned blocks every turn and renders
them into the system prompt under a section the model is told to
treat as **ground truth that beats anything in auto-recall**.

---

## Decision: when do I use blocks vs episodes (Graphiti)?

One sentence rule:

> If the subject is `I` / `me` / `the user` and the predicate is a
> preference, identity, current state, or routine, it's a **block**.
> Otherwise it's an **episode**.

Examples:

| User says | Goes to | Why |
|---|---|---|
| "Call me 黑仔" | block `preferences.nickname_for_assistant` | first-person + preference |
| "My name is Jerry" | block `persona.name`, `scope=global` | first-person + identity |
| "I prefer dark mode" | block `preferences.ui_theme` | first-person + preference |
| "I'm working on wad-ssl-crash" | block `state.current_focus` | first-person + state |
| "Sarah teaches Portuguese" | episode | third-party fact |
| "Bruno is Anna's student" | episode | third-party relation |
| "We shipped feature X last quarter" | episode | event with time |

---

## Why this exists

### What we tried first

We tried Graphiti's normal episode pipeline. Episode content
`用户想叫我黑仔` ("user wants to call me Heizai"), source_type=message.

Three structural failures stacked:

1. **`extract_message` filters pronouns by design** (`prompts/extract_nodes.py:91`):
   > NEVER extract any of the following:
   > - Pronouns (you, me, I, he, she, they, we, us, it, them, …)

   So "我" (me, = the assistant) is never an entity. There's no
   "Smith" node in the graph.

2. **`extract_edges` requires two distinct entities** (`prompts/extract_edges.py:148`):
   > Each fact must involve two **distinct** entities — `source_entity_name`
   > and `target_entity_name` NEVER refer to the same entity.

   With only `User` and `Heizai` (a nickname value), the LLM was
   forced to build `User → ? → Heizai` and natural-language it as
   "the user wants to be called heizai" — agency flipped.

3. **Summary update is append-only string concatenation**
   (`utils/maintenance/node_operations.py:854-862`):
   ```python
   if summary_with_edges and len(summary_with_edges) <= MAX_SUMMARY_CHARS * 2:
       node.summary = summary_with_edges   # no LLM rewrite
       continue
   ```

   Once a wrong fact landed in `node.summary`, every new episode
   could only APPEND, never rewrite. The wrong text persisted
   forever unless we manually invalidated + resummarized via the
   admin endpoint we built (`POST /v1/admin/entities/<uuid>/resummarize`).

### Why correction didn't fix it for free

We did write a `memory_correct_apply` tool to invalidate + rewrite +
resummarize. It works for one-shot corrections. But every new
re-statement of the same preference (the user saying "call me X"
again later in a different way) re-triggered the same agency-flip
pipeline. So we kept fighting the same bug per statement.

The agent could also do nothing about the underlying problem —
Smith faithfully writes what the user said; Graphiti reinterprets
it on extraction.

### What we considered before settling on blocks

| Option | Why we didn't pick it |
|---|---|
| Patch Graphiti's prompts to allow pronoun extraction + self-entity | Brittle (every Graphiti upgrade breaks the patch); and the underlying graph model still wants two-distinct-entity facts |
| Pre-register entity schemas (`UserProfile` with typed `nickname` fields) | Helps but each new "kind of preference" needs a new schema; doesn't dodge the summary-append behavior |
| New SQL table per kind of preference | The architecture concern: 10 tables in 6 months |
| Document store (Obsidian / markdown) | Too heavy; this is single-key value not long-form |
| Just put it in system prompt config | Doesn't scale across users / agents; can't be edited by the agent |
| Memory blocks | What we built ✓ |

### Why blocks doesn't become its own zoo

The reason `memory_blocks` is one table, not N: **the schema is
the value**. `block_value` is JSONB. Any future need — coffee
preferences, oncall schedule, internal Jenkins URL — fits as a new
key. No new table, no migration.

The convention is the prefix (`preferences.*`, `state.*`, etc),
which is documentation only.

### Reference: prior art

Most modern agent memory frameworks land on the same "one structured
store, agent-defined keys" pattern:

- **Letta** (MemGPT successor) — `core_memory` blocks; agent-defined
  names like `persona`, `human`, `current_task`.
- **OpenAI Memories** — flat bullet list, LLM uses add/update/delete tools.
- **Claude Code auto-memory** — file system with categorized markdown
  files (`user_*.md`, `feedback_*.md`).

`memory_blocks` is closest to Letta's design — structured per-key,
JSONB value, agent maintains via tools.

---

## Implementation pointers

| Concern | Where |
|---|---|
| Migration | `src/memory_service/db/migrations/versions/0012_memory_blocks.py` |
| ORM model | `src/memory_service/models/memory_block.py` |
| CRUD | `src/memory_service/core/blocks.py` |
| HTTP router | `src/memory_service/api/v1/blocks.py` |
| Admin UI page | `src/memory_service/web/templates/blocks.html` (`/admin/blocks`) |
| CLI | `memctl blocks {ls,get,set,patch,rm,pin,unpin}` |
| Smith tools | `agents/smith/src/extensions/temper-memory.ts` (4 tools) |
| Smith system prompt injection | `agents/smith/src/extensions/smith-personality.ts` (`before_agent_start` hook) |
