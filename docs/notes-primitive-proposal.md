# TEMPER notes primitive — design proposal

## Context

Smith / future chrome-extension agents need somewhere to put
**long-form structured content**: meeting transcripts, project
notes, research dumps, the user's own writing. Today TEMPER has
three primitives:

- **episodes** (graphiti): short structured observations
- **memory_blocks**: KV first-person assertions
- **typed memory** (tasks / focus / preferences / events): typed
  wrappers over the above

None fit long-form markdown. Episodes are noisy when fed page-size
text (extraction collapses or chokes). Blocks are KV, not editable
documents.

The chrome-extension agent worsens this: it can't easily reach the
user's local Obsidian vault from a browser sandbox. Centralizing
markdown storage in TEMPER fixes the access problem and gives
agents a uniform "document" primitive across hosts.

This doc proposes adding a fourth primitive: **`notes`** —
markdown documents with stable paths, wiki-links, and full-text +
semantic search, served from TEMPER over HTTP.

## Why DB-as-storage (not filesystem)

| | Markdown in Postgres TEXT col | Markdown files on FS |
|---|---|---|
| Single source of truth | ✓ (one DB) | needs sync layer |
| Multi-user perm | row-level | dir trees per user |
| Backup | `pg_dump` covers everything | DB + FS + matching versions |
| Chrome extension access | trivial HTTP | needs file server / mount |
| Sync with user's local Obsidian | export-on-demand | bidirectional sync is hard |
| Editor UX (admin UI) | TEXTAREA → POST → done | round-trip via FS |
| Version history | revisions table | git OR DIY |

DB wins decisively for the "central service consumed by remote
agents" use case. The cost is that the user's existing local
Obsidian vault isn't auto-synced — they import once, then TEMPER
is canonical. Acceptable for v1.

If somebody wants offline-edit-in-Obsidian later, we can add a
sync daemon. Premature to design that now.

## Schema

```sql
CREATE TABLE notes (
  id              UUID PRIMARY KEY,
  user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  namespace       TEXT NOT NULL,           -- matches TEMPER's existing scheme:
                                           -- user:<uuid> / agent:<uuid>/<slug> / group:<slug>

  path            TEXT NOT NULL,           -- "projects/auth/refactor"
                                           -- (no extension; .md is implied)
  title           TEXT NOT NULL,           -- first H1 or filename fallback
  content         TEXT NOT NULL,           -- the raw markdown body

  frontmatter     JSONB,                   -- parsed YAML frontmatter
  tags            TEXT[],                  -- denormalized from frontmatter for indexing

  -- Search indexes
  content_tsv     tsvector,                -- generated, Postgres FTS
  embedding       vector(1536),            -- pgvector, optional (added in phase 2)

  -- Bookkeeping
  word_count      INTEGER NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by      TEXT,                    -- "user:<email>" / "agent:<slug>" / "import:<source>"

  CONSTRAINT notes_path_uniq UNIQUE (user_id, namespace, path)
);

CREATE INDEX ix_notes_tsv ON notes USING gin(content_tsv);
CREATE INDEX ix_notes_tags ON notes USING gin(tags);
CREATE INDEX ix_notes_embedding ON notes USING hnsw(embedding vector_cosine_ops);
CREATE INDEX ix_notes_ns_path ON notes(user_id, namespace, path);
CREATE TRIGGER notes_update_tsv BEFORE INSERT OR UPDATE ON notes
  FOR EACH ROW EXECUTE PROCEDURE tsvector_update_trigger(content_tsv, 'pg_catalog.simple', title, content);
```

```sql
-- Materialized links — parsed on each save, drives backlinks.
-- One row per (source_note, target_path) pair found in source content.
CREATE TABLE note_links (
  source_note_id  UUID REFERENCES notes(id) ON DELETE CASCADE,
  target_path     TEXT NOT NULL,           -- normalized [[wikilink]] target
  label           TEXT,                    -- if [[target|label]]
  PRIMARY KEY (source_note_id, target_path, label)
);

CREATE INDEX ix_note_links_target ON note_links(target_path);

-- Revisions — keep last N edits per note for "what did this say
-- last week". Pruned by a cron / consolidation pass.
CREATE TABLE note_revisions (
  id              UUID PRIMARY KEY,
  note_id         UUID NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
  content         TEXT NOT NULL,
  title           TEXT NOT NULL,
  revised_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  revised_by      TEXT
);
CREATE INDEX ix_note_revisions_note ON note_revisions(note_id, revised_at DESC);
```

Namespace partitioning matches the existing graphiti / blocks
model — `user:<uuid>` for personal notes, `agent:<uuid>/<slug>`
for agent-private scratch, `group:<slug>` for team-shared.

## API

```
GET    /v1/notes                                  list (filter ?prefix=, ?tags=, ?namespace=)
GET    /v1/notes/{path:path}                      one note (?revision=N for history)
PUT    /v1/notes/{path:path}                      upsert (full content replace)
PATCH  /v1/notes/{path:path}                      partial (append, replace section, set tags)
DELETE /v1/notes/{path:path}                      drop (+ cascade revisions, links)

GET    /v1/notes/{path:path}/backlinks            who links to this path
GET    /v1/notes/{path:path}/revisions            history (titles + dates only)
GET    /v1/notes/{path:path}/revisions/{id}       one historical version

GET    /v1/notes/search?q=...&kind=fts|vector|hybrid    full-text + vector search
                                                  fts: tsvector  · vector: embeddings
                                                  hybrid: RRF over both

POST   /v1/notes/import                           bulk upload — body: { vault_path?: str,
                                                  notes: [{path, content, frontmatter, tags}, ...] }
                                                  used for "import my Obsidian vault" one-shot
```

All same-shape auth as the rest of TEMPER (API key → namespace
scope, session auth → user-wide).

## Wiki-link semantics

On save, the server parses the content for `[[target]]` and
`[[target|label]]` patterns, writes the join table. Backlinks are
just `SELECT source FROM note_links WHERE target_path = ?`.

Link resolution: case-sensitive exact path match by default.
Future addition: alias table for `[[Bob]]` → `people/bob.md`.

For unresolved links (target doesn't exist yet): still tracked.
Lets the UI render them in a different style ("create note 'X'?").

## Integration with existing primitives

### graphiti episodes

Each note save writes a TINY summary episode under the note's
namespace, tagged `note-update`:

```python
add_episode(
  namespace=note.namespace,
  content=f"Note {note.path} updated: {note.title}. First 200 chars: {snippet}",
  tags=["note-update"],
  source_description=f"note:{note.path}",
  reference_time=note.updated_at,
)
```

Why: graphiti's semantic recall can then surface "you have a note
about X" hits on queries that don't mention the path literally.
The episode is small (no full content), so extraction cost is low.

### memory_blocks

Blocks can reference notes:

```jsonc
{
  "block_key": "state.current_project",
  "block_value": {
    "name": "auth-refactor",
    "notes": ["projects/auth", "decisions/jwt-vs-session"]
  },
  "pinned": true,
  "priority": 100
}
```

Smith's `before_agent_start` already injects pinned blocks. The
agent can then `note_open("projects/auth")` when relevant.

### typed memory layer

Three new typed tools:

```
POST /v1/memory/notes/save        { path, content, tags? } → note
POST /v1/memory/notes/append      { path, content }        → note
GET  /v1/memory/notes/search      ?q=...                   → [{path, title, snippet}]
GET  /v1/memory/notes/open        ?path=...                → { path, content, backlinks }
```

These are thin wrappers, but they slot into the existing
`task_add` / `set_focus` / `note_event` tool family so the
agent's mental model stays uniform: "I'm calling a typed memory
function, TEMPER decides where it lands."

## Embedding strategy (phase 2)

Three options for what to embed:

| Approach | Cost / note | Recall quality | Implementation |
|---|---|---|---|
| Whole-doc | 1 embedding call | weak for long docs | trivial |
| Chunked (paragraph) | N calls per doc | strong | need chunk table |
| Title + summary | 1 cheap LLM + 1 embed | medium | LLM summarization cost |

Recommend: **chunked**, paragraph-level (or H2-section). Store
in a `note_chunks` table with `note_id`, `chunk_idx`,
`content`, `embedding`. Search returns chunk hits which the UI
resolves back to source notes.

Defer to phase 2 — FTS via tsvector is good enough for MVP.

## Phasing

| Phase | Scope | Effort |
|---|---|---|
| N1 — MVP | schema, CRUD, FTS search, wiki-link parsing + backlinks | 2-3d |
| N2 — Embeddings | chunks table, embedding pipeline, hybrid search | 1-2d |
| N3 — Admin UI | /admin/notes (tree + editor + preview + backlinks panel) | 1-2d |
| N4 — Obsidian import | bulk endpoint + a `memctl notes import <vault_dir>` CLI helper | 0.5d |
| N5 — Agent integration | typed memory tools + chrome-extension UI for browse / edit | 1d |
| N6 — Revisions UI | history viewer in admin; prune-old job | 0.5d |
| N7 — Future | LLM auto-summary, alias resolution, OFFLINE Obsidian sync daemon | open |

N1+N4+N5 is the minimum to be useful (~4d): you can import your
existing Obsidian vault, the agent can read/write notes, the
extension UI can display them.

## Open decisions

1. **Single shared namespace, or per-agent?** — I'd default to
   `user:<uuid>` for notes (the user is the author). Agent's own
   scratch could live under `agent:<uuid>/<slug>` if it wants
   private memos.

2. **Markdown flavor?** — pick CommonMark + GFM (tables, task
   lists, autolinks) and Obsidian's `[[wikilink]]`. No `![[embed]]`
   or `^^block-ref^^` yet (Obsidian-specific, defer).

3. **Where do attachments go?** — images, PDFs. Defer to phase
   later. v1 stores text only; images can be inlined as URLs to
   external storage if user wants.

4. **Note size limits?** — Suggest 1MB content per note, soft
   warn at 100KB. Bigger = probably should be split.

5. **Backup story?** — `pg_dump` covers it. For paranoid users a
   future `memctl notes export <vault_dir>` writes the whole tree
   to disk as .md files.

## What this is NOT

- **Not a real-time collaborative editor.** One user, one writer.
  Concurrent edits = last-write-wins (with revision trail so
  nothing is lost).
- **Not a replacement for Obsidian on the user's machine.** If
  the user wants the Obsidian app's UX + plugins, they keep
  Obsidian; TEMPER's notes is a parallel, agent-accessible
  vault. We provide one-shot import + future export, not
  bidirectional sync.
- **Not a CMS / public-facing wiki.** Per-user scoped. No
  publishing flow.

## Why this is the right call (vs alternatives)

Alternatives considered:

**A. Skip it; agents just write big episodes** — Tried this,
   failed. Graphiti's extractor doesn't handle 1000-word inputs
   well; the entity / fact decomposition becomes noisy or
   collapses. Episodes are for "short structured observations",
   not long-form.

**B. Use a filesystem-backed vault on the TEMPER host** —
   Doubles the operational surface (DB + FS in sync). Multi-user
   row-level permissions become directory ACLs. Backup becomes
   pg_dump + rsync. Worse on every axis except "user can ssh in
   and read .md files".

**C. Let agents store long text as blocks** — Blocks are designed
   for small JSONB values. Stuffing 5KB markdown into a block's
   `value` works once but breaks the "pinned blocks fit in
   prompt budget" invariant. Wrong primitive.

**D. External service (Notion / Obsidian Sync / Logseq)** —
   Auth, vendor lock-in, latency, and the agent has to learn N
   APIs. Not aligned with TEMPER's "one HTTP surface" model.

**The proposal (notes primitive in TEMPER)** keeps everything
local to one service, fits the existing namespace model, slots
into the typed memory layer cleanly, and gives the chrome-
extension agent a uniform document API regardless of host.

---

## Next step (if you want to proceed)

Decisions needed:
- approve N1 scope?
- default namespace for the user's notes (I'd say `user:<uuid>`)
- markdown flavor (I'd say CommonMark + GFM + `[[wikilinks]]`)
- attachment policy for v1 (text-only is fine, link to external?)

Once decided, N1 is the migration + core + 6 HTTP endpoints +
parser. ~2-3 days of work.
