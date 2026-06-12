# TEMPER API reference (for client implementers)

This is for engineers building a **programmatic client** against TEMPER
(e.g. the Forge tool layer) — not the prompt-only integration. Wrap
these endpoints as tools and the agent calls real functions instead of
improvising HTTP from a prompt.

**Machine-readable source of truth** (always in sync with the running
build):

- OpenAPI JSON: `GET {base}/openapi.json` — generate a typed client from it
- Swagger UI:    `{base}/docs`
- ReDoc:         `{base}/redoc`

This doc curates the **agent-relevant subset** with the shapes + gotchas
that the OpenAPI schema alone won't tell you (the `skipped` flag,
async extraction, namespace resolution, etc.). When this doc and
`/openapi.json` disagree, the spec wins.

---

## 0. Conventions

- **Base URL**: `http://<host>:18088` (whatever `MS_PORT` resolves to).
- **Auth** — every business endpoint accepts either:
  - `X-API-Key: mk_...` — for agents/services (from `/v1/users/me/api-keys`)
  - `Authorization: Bearer <jwt>` — for humans/console (from `/v1/auth/login`)
  - API key wins if both are present.
- **Errors**: `{"detail": "..."}` (a 422 from request validation is a
  list of `{loc, msg, type}` instead).
- **Times**: ISO-8601 UTC everywhere.
- **Content-Type**: `application/json` on every body.

### Getting an API key

A human logs in once and mints a key the agent then uses forever:

```bash
JWT=$(curl -s -X POST {base}/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"..."}' | jq -r .access_token)

curl -s -X POST {base}/v1/users/me/api-keys \
  -H "Authorization: Bearer $JWT" -H 'Content-Type: application/json' \
  -d '{"agent_name":"forge-agent","agent_slug":"forge-agent"}'
# → { "key": "mk_...", "agent_slug": "forge-agent", "prefix": "mk_...", ... }
```

The plaintext `key` is shown **once**. `agent_slug` is slugified
server-side (any input is normalized; send `null` for an unscoped key).
**Multiple keys may share one slug** — that's how several agents/machines
write into one memory namespace; revoking one doesn't cut off the rest.

---

## 1. Namespaces — where memory lives

Every write/read targets a namespace. **Omit `namespace` and the
key's default is used**: `agent:<user_id>/<slug>` when the key has a
slug, else flat `user:<user_id>`.

| Shape | Meaning |
|---|---|
| `user:<id>` / `user:me` | the user's flat namespace, shared across ALL their agents |
| `agent:<id>/<slug>` / `agent:me/<slug>` | one named agent; isolated unless another key shares the slug |
| `group:<slug>` | shared inside a team (members read+write) |
| `org:<slug>` | members read; super_admin writes |
| `public` | everyone-authenticated read |

`GET {base}/v1/namespaces` lists what the caller can read.

---

## 2. Episodes — the history primitive (graphiti)

Free-text events → extracted into entities + facts.

### Write

```
POST {base}/v1/episodes[?async_extract=true]
{
  "content": "binxu owns the FortiNAC 7.6 build pipeline",
  "source_type": "message",          // "message" | "text" | "json"
  "source_description": "user chat",
  "reference_time": "2026-06-12T10:00:00Z",   // when it HAPPENED
  "tags": ["ownership"],
  "saga": "fortinac-7.6",            // optional: chain episodes
  "namespace": "agent:me/forge-agent" // optional; default used if omitted
}
```

Response (201 — sync):
```json
{
  "episode_id": "…",
  "namespace": "agent:…/forge-agent",
  "extracted_entities": [{"uuid","name","labels","summary"}],
  "extracted_facts":    [{"uuid","fact","source_entity_uuid","target_entity_uuid","valid_at","invalid_at"}],
  "created_at": "…",
  "skipped": false,
  "skip_reason": null
}
```

**Two gotchas the client MUST handle:**

1. **`skipped: true` → HTTP 200, not 201.** The server rejected the write:
   - content below the quality floor (`episode_id` is `""`), or
   - a duplicate within the dedup window (`episode_id` is the EXISTING
     episode's id).
   Treat `skipped:true` as "don't retry; the content was junk/dup",
   NOT as a transient failure. `skip_reason` explains which.

2. **`?async_extract=true` → HTTP 202**, returns immediately with empty
   entities/facts and the extraction running in the background. Poll
   `GET /v1/episodes/{id}/status` → `{extraction_status: pending|done|failed}`.
   Use for bulk writes where you don't need facts back in the same call.

### Bulk write

```
POST {base}/v1/episodes/bulk
{ "namespace": "...", "saga": "...", "items": [ {content, source_type, ...}, ... ] }   // ≤200
```
Response: `{episode_ids:[...], total_entities, total_facts, skipped_count}`.
The floor + dedup apply per item (plus intra-batch dedup); dropped items
are counted in `skipped_count` and excluded from `episode_ids`.

### Read / list / delete

```
GET    {base}/v1/episodes?namespace=<ns>&limit=100[&before=<cursor>]
GET    {base}/v1/episodes/{id}            # metadata + content + entities + facts
GET    {base}/v1/episodes/{id}/status     # cheap SQL — extraction_status
DELETE {base}/v1/episodes/{id}            # removes the graph node + metadata
```

---

## 3. Search — semantic + graph, bi-temporal

```
GET {base}/v1/search?query=<text>&limit=10
```
Optional knobs:
| param | effect |
|---|---|
| `namespaces=a,b,c` | restrict scope |
| `node_labels=Person,Project` | only these entity kinds |
| `edge_types=OWNS,WORKS_ON` | only these relation names |
| `as_of=<ISO>` | point-in-time: what was true at T |
| `center=<entity_uuid>` | bias ranking around a node |
| `bfs_origins=<a,b>&bfs_max_depth=2` | graph walk from seed UUIDs |
| `reranker=rrf\|mmr\|cross_encoder` | rrf=fast (default); cross_encoder=LLM-rescored |
| `min_score=<0..1>` | server-side floor (only with `cross_encoder`) |

Each hit: `{fact, score, valid_at, invalid_at, id, source_node_uuid,
target_node_uuid, kind, namespace}`. `kind` is `fact` (edge) or
`entity` (node) or `community`. `id` is the edge/entity UUID — feed it
straight to the temporal/correction endpoints below.

---

## 4. Facts — temporal correction

Usually unnecessary (writing a contradicting episode auto-invalidates).
Use when you have authoritative knowledge of when a fact stopped being true:

```
GET    {base}/v1/facts/{uuid}
PATCH  {base}/v1/facts/{uuid}   { "invalid_at": "<ISO>" }   // null = reactivate
DELETE {base}/v1/facts/{uuid}
```
Full correction flow: search → `PATCH invalid_at` → write corrected
episode → `POST /v1/admin/entities/{uuid}/resummarize` (rebuilds the
entity summary so stale text doesn't linger).

---

## 5. Memory blocks — the KV state primitive

For FIRST-PERSON assertions graphiti mangles (preferences, identity,
current focus). Flat JSONB key/value, scoped per (user, agent_slug).

```
GET    {base}/v1/memory/blocks?scope=both[&pinned=true][&prefix=...]
GET    {base}/v1/memory/blocks/{key}?scope=own
PUT    {base}/v1/memory/blocks/{key}    { "value": <any>, "pinned"?, "priority"?, "description"?, "scope"? }
PATCH  {base}/v1/memory/blocks/{key}    { "value": <partial> }   // deep JSONB merge
DELETE {base}/v1/memory/blocks/{key}?scope=own
```
`scope`: `own` (caller's agent_slug) | `global` (`*`, all agents) | `both` (list only).
`pinned:true` → auto-injected into the agent's prompt every turn (use sparingly).

> **Discipline**: blocks are for a few dozen STATE keys. Do NOT write
> `fact:*` blocks (facts go to episodes) or per-turn `chat:*` summary
> blocks (use documents). See `docs/agent-integration-prompt.md`.

### Typed memory (sugar over blocks + episodes)

Higher-level intent endpoints — prefer these over raw blocks:
```
GET/POST  {base}/v1/memory/tasks                 # list / add
PATCH     {base}/v1/memory/tasks/{id}            # update
POST      {base}/v1/memory/tasks/{id}/complete
GET/PUT   {base}/v1/memory/focus                 # current focus
GET       {base}/v1/memory/preferences           # list
PUT       {base}/v1/memory/preferences/{key}
POST      {base}/v1/memory/events                # note_event → episode
GET       {base}/v1/memory/turn_context          # everything to inject this turn
```
`GET /v1/memory/turn_context` is the one-shot "what should I load into
the prompt right now" call: pinned blocks + active tasks + focus + prefs.

---

## 6. Documents — the markdown wiki primitive

Long-form addressable content (saved tickets, reports, SOPs, chat
archives). Markdown, stable `path`, `[[wikilink]]`-aware, full-text
searchable.

```
GET    {base}/v1/documents?namespace=<ns>&limit=50[&prefix=][&tags=][&source=]
GET    {base}/v1/documents/search?q=<text>&namespace=<ns>
PUT    {base}/v1/documents/{path}    { "title", "content", "content_type"?, "source"?, "source_url"?, "tags"?, "frontmatter"? }
PATCH  {base}/v1/documents/{path}    { partial fields }
GET    {base}/v1/documents/{path}
DELETE {base}/v1/documents/{path}
GET    {base}/v1/documents/{path}/backlinks
GET    {base}/v1/documents/{path}/revisions[/{revision_id}]
POST   {base}/v1/documents/import    { "documents": [ {path, title, content, ...}, ... ] }
```
`PUT` is upsert (revisions kept automatically on overwrite). Paths are
filesystem-style (`chats/<id>.md`) or dotted (`status.weekly`). Use this
for chat summaries: ONE doc per chat, overwrite with the latest.

---

## 7. Sagas + entity schemas

```
GET  {base}/v1/sagas?namespace=<ns>            # chains created by the `saga` write field
GET  {base}/v1/sagas/{name-or-uuid}?namespace=<ns>

GET    {base}/v1/schemas/entity-types?namespace=<ns>
POST   {base}/v1/schemas/entity-types?namespace=<ns>
       { "name":"Project", "description":"…",
         "fields":[ {"name":"owner","type":"string","required":true}, ... ] }
GET    {base}/v1/schemas/entity-types/{name}?namespace=<ns>
DELETE {base}/v1/schemas/entity-types/{name}?namespace=<ns>
```
Register a schema BEFORE writing episodes about that entity kind so
extraction stays consistent. Field types: string, integer, number,
boolean, datetime.

---

## 8. Communities — clustered summaries (async)

Expensive (clustering + LLM-per-cluster). **Runs in the background**:

```
POST {base}/v1/admin/communities/build?namespace=<ns>      # → 202 {status:"running"}
GET  {base}/v1/admin/communities/build/status?namespace=<ns>
     # → {status: idle|running|done|failed, communities_created?, community_edges_created?, error?}
```
A second build on a namespace that's already building → **409**. Poll
the status endpoint; don't block on the POST.

---

## 9. Backup + portability (per-user)

### Snapshots — point-in-time restore

```
GET    {base}/v1/me/snapshots                    # list (newest first, metadata only)
POST   {base}/v1/me/snapshots   { "note"?, "include_episodes"? }   # take one now
GET    {base}/v1/me/snapshots/{id}               # full bundle download
POST   {base}/v1/me/snapshots/{id}/restore?mode=merge|replace
DELETE {base}/v1/me/snapshots/{id}
```
The server auto-snapshots blocks + documents daily; `include_episodes`
opts into episodes (slower; restore re-extracts via LLM). Restore is
merge (default) or replace.

### Export / import — move memory between instances

```
GET  {base}/v1/me/export                         # whole bundle (blocks+documents+episodes)
POST {base}/v1/me/import?mode=merge|replace[&background_extraction=true]
```
`scripts/migrate.py` wraps these for host-to-host moves.

---

## 10. Onboarding (super_admin) — one-call user provisioning

```
POST {base}/v1/onboarding/provision
{ "username", "email", "company", "dept", "display_name"? }
```
Get-or-creates org + group by slug, creates the user with a starter
password, issues an API key scoped to the username. Returns the
plaintext `api_key` + `default_password` ONCE. super_admin only — the
caller (e.g. Forge onboarding) holds a super_admin key. See the earlier
commit for the full response shape.

---

## 11. Stats + health

```
GET {base}/v1/stats                  # episode counts, zero-yield, users/orgs/groups (super_admin)
GET {base}/v1/stats/episodes/daily?days=30
GET {base}/v1/health[?deep=true]     # deep=true actually probes the LLM + embedder
```

---

## 12. Implementation notes for a client

- **Idempotency**: episode writes dedup by content hash within a window;
  a retry of the same content returns the existing id with `skipped:true`,
  so naive retries are safe.
- **Don't poll FalkorDB-heavy reads in a tight loop**: `/v1/graph`,
  `/v1/graph/cypher`, and per-episode detail reads hit a single-worker
  graph DB. Batch where you can.
- **Respect `skipped` / `409` / `423`**: skipped=write rejected;
  409=community build already running; 423=namespace sleeping
  (consolidation in progress) — back off and retry.
- **Namespace once**: set the key's `agent_slug` and omit `namespace`
  on every call rather than threading it through each request.
- Generate a typed client from `/openapi.json` and layer these
  behaviors on top.
