# Memory frameworks — mem0 vs graphiti

Recap of the decision that picked graphiti for TEMPER, and an
honest re-look in light of the forge pivot to a browser-extension
data-source model.

> Status 2026-05-16: TEMPER is on graphiti. This doc exists to
> serve as the source-of-truth comparison and revisit whether
> forge should adopt the same stack or pick differently.

---

## At a glance

| Dimension                | mem0                          | graphiti                                    |
|---|---|---|
| Storage backend          | vector DB (pgvector / qdrant / chroma) | graph DB (Neo4j / FalkorDB / Kuzu)        |
| Primary primitive        | "memory" (text + embedding)   | episode → entities + relations + facts      |
| Extraction at write      | 1 LLM call (classify, optional dedup) | 2–3 LLM calls (entity extract + edge extract + summary) |
| Search                   | vector + optional reranker    | hybrid BM25 + cosine + graph distance, 3 rerankers |
| Temporal model           | snapshot (no time-travel)     | bi-temporal: valid_at + invalid_at per fact |
| Conflict handling        | UPDATE replaces               | new fact invalidates old; both stay queryable |
| Entity reasoning         | weak (entities are tags)      | first-class (nodes + typed edges)           |
| Multi-tenant keys        | user_id / agent_id            | namespace / group_id                        |
| Infra footprint          | pgvector ≈ Postgres + 1 ext   | Postgres + FalkorDB (Redis-protocol graph)  |
| Write throughput         | high (single LLM call)        | low (multi-pass extraction)                 |
| Write cost per episode   | ~1¢ at GPT-4o-mini rates      | ~3–5¢ (3 LLM calls, longer prompts)         |
| Read latency             | ~50–200ms                     | ~100–500ms (multi-index fan-out)            |
| Maturity                 | older, broader adoption       | newer, fast-moving, more research-y         |
| API ergonomics           | simple (.add / .search / .update / .delete) | richer surface, more knobs to learn |

---

## mem0 — what it is

A Python library that treats memory as **text + embedding +
metadata**, with an LLM-driven extraction layer on top. Workflow:

```
.add(messages=[...], user_id="alex")
  → LLM classifies the conversation into N "memories"
  → embedding-encode each
  → store in vector DB with metadata

.search("what does alex like for breakfast", user_id="alex")
  → embed the query
  → vector search top-K
  → optional rerank
  → return text + metadata
```

### Strengths

- **Cheap writes.** One LLM call per message batch to classify
  what's worth remembering vs noise.
- **Simple ops.** pgvector is "Postgres with a `vector` column" —
  no separate DB cluster to deploy/monitor.
- **Battle-tested.** More production deployments than graphiti;
  more Stack Overflow / GitHub issue answers.
- **Easy API.** `.add()`, `.search()`, `.update()`, `.delete()` —
  four verbs and you're integrated.
- **Built-in graph mode (newer).** Added in late 2025; basic
  relation extraction layered on top of the memory primitive.
- **Strong dedup.** Built-in semantic dedup at write time —
  re-adding "I live in Lyon" won't store a duplicate.

### Weaknesses

- **No time-travel.** A memory has one current value. If the user
  says "I moved from Lyon to Paris", the old "Lyon" memory either
  gets overwritten or sits there as a stale duplicate. You can't
  query "what was true 6 months ago".
- **Weak relation reasoning.** Entities are tags, not graph nodes.
  "Who teaches Bruno?" is harder to answer than "things about
  Bruno"; the answer might live in a single embedded blob you
  retrieve by similarity, but you can't graph-walk from `Bruno` →
  `LEARNS_FROM` → `Sarah`.
- **Black-box extraction.** The classifier prompt is the library's
  default. Tuning it for company-specific patterns (Mantis bug
  format, internal acronyms) means forking or wrapping.
- **Conflict policy is "overwrite".** No bi-temporal trail. If
  your auditor asks "when did this fact change", you don't have
  the data.

---

## graphiti — what it is

A Python library from Zep that treats memory as a **knowledge
graph with bi-temporal validity**. Workflow:

```
graphiti.add_episode(
  group_id="agent:alice/smith",
  episode_body="Bruno is Sarah's student in Lyon",
  source_description="from chat with Alice",
)
  → LLM call 1: extract entities  (Bruno, Sarah, Lyon)
  → LLM call 2: extract edges     (Bruno LEARNS_FROM Sarah,
                                   Sarah LIVES_IN Lyon)
  → LLM call 3: build / update entity summaries
  → write nodes + edges to graph DB with valid_at = now

graphiti.search("who teaches Bruno", group_id="agent:alice/smith")
  → BM25 over edge labels
  → vector search over entity summaries
  → graph BFS from candidates
  → rerank (rrf / mmr / cross_encoder)
  → return ranked facts
```

### Strengths

- **Bi-temporal.** Every edge has `valid_at` + `invalid_at`. You
  can ask "what was true on March 15" and get the right answer.
  When a new fact contradicts an old one, graphiti auto-invalidates
  the old (sets `invalid_at = now`) instead of deleting it. Audit
  trail comes for free.
- **First-class relations.** Bruno is a `Person` node with edges
  to other nodes. "Tell me everything connected to Bruno" = BFS
  from Bruno's UUID. Useful for entity-centric agents.
- **Multiple rerankers.** rrf (free, rank-based), mmr (diversity),
  cross_encoder (LLM-rescored, true [0,1] relevance). Tunable
  per-query.
- **Multi-namespace.** `group_id` partitions the graph — same
  schema can hold one user's data, one agent's data, public
  encyclopedic data, all queryable independently or in union.
- **Active research.** Zep ships features fast; the library is
  evolving (community detection, schema-typed entities, search
  recipes).

### Weaknesses

- **Heavy writes.** 3 LLM calls per episode. Indexing a year of
  email costs real money + is slow. Bulk import is painful.
- **Extra DB.** FalkorDB (Redis-protocol graph) or Neo4j alongside
  Postgres. One more process to run, monitor, back up.
- **Extraction quality varies.** TEMPER hit the "agency flip"
  bug — model summarized "user calls me Heizai" as "user wants to
  be called Heizai", because the entity-summary LLM call doesn't
  always preserve the subject. Real production issue; required
  building `memory_blocks` as a second primitive to bypass the
  extractor for first-person assertions.
- **Search complexity.** The same answer can hit through fact-kind
  edge, entity-kind node summary, or community-kind cluster. You
  have to dedup at the client layer (smith-personality.ts has
  this whole "drop entity hits whose lines duplicate fact hits"
  reducer because of it).
- **Costlier per-query.** Multi-index fan-out + reranker = ~3x
  what a single pgvector lookup costs.
- **Smaller ecosystem.** Fewer adapters, less middleware, fewer
  blog posts on "how I made it work in prod".

---

## TEMPER's actual choices in 2026-05

What we ended up with after running both paths in our heads + one
of them (graphiti) in code:

1. **graphiti for episode + entity + fact memory.** Strong for the
   agent's curated, structured-ish data: "Sarah teaches Portuguese",
   "the wad-ssl bug hit prod on T", decisions, project state.
2. **memory_blocks for first-person assertions.** Built as a
   second primitive after graphiti's extractor kept flipping
   pronouns on user identity facts (nickname, preferences,
   current focus). Plain JSONB KV. See `docs/memory-blocks.md`.
3. **typed memory layer** on top of both. Agents call
   `task_add()` / `set_focus()` / `note_event()`; TEMPER decides
   where to land (block vs graphiti episode).

The takeaway: **graphiti alone wasn't enough for our use case**.
The bi-temporal + entity-relation model is great when the data is
"events about people/projects" but bad when the data is "the user
saying something about themselves". The second pattern is too
common to leave broken, so we added blocks.

mem0 would have had the same first-person-assertion problem if
used alone — its classifier would dedup or paraphrase the
nickname declaration. Neither library handles this cleanly
without a second primitive.

---

## Forge — chrome-extension data source — which one?

Forge's pivot context (per 2026-05-16):

- Drop MCP entirely (auth was a pain, integration unstable).
- Chrome extension ingests **browser data**: page content, URLs,
  reading history, possibly Gmail snippets, calendar entries.
- Volume: **high** (every page visited could become an episode).
- Structure: **low** — mostly free-form page text + metadata.
- Query pattern: "what was that article about X I read last
  Tuesday" (mostly recency + topical search, not entity-centric).
- Latency target: probably async ingest, real-time read.

### Cost analysis

Assume the user reads 50 web pages a day worth indexing.

| Library     | LLM cost / day                   | Storage cost      | Read cost / query |
|---|---|---|---|
| graphiti    | 50 × 3 calls × ~1500 input tokens = ~225k tok = ~$0.30/day at GPT-4o-mini | pgvector + FalkorDB | ~3 LLM rerank calls |
| mem0        | 50 × 1 call × ~1000 tokens = ~50k tok = ~$0.07/day | pgvector only | embedding + 0-1 LLM |
| Custom thin | 50 × 0 LLM = $0 (just embed)     | pgvector only     | embedding only    |

Over a year: graphiti ≈ $110, mem0 ≈ $25, thin ≈ $0 + embedding API.

### Fit analysis

| Need                                  | graphiti           | mem0               | Custom thin (pgvector + embeddings) |
|---|---|---|---|
| High write throughput                 | ❌ slow            | ✓ ok               | ✓ fastest                            |
| Bi-temporal queries ("last Tuesday")  | ✓ native          | ❌ no              | could add `seen_at` column           |
| Entity reasoning ("about Sarah")      | ✓                 | weak                | weak                                  |
| Conflict resolution                   | ✓ auto             | overwrite           | none                                  |
| Operationally simple                  | ❌ FalkorDB        | ✓ pgvector         | ✓ pgvector                            |
| Strong dedup of repeat pages          | extractor decides | ✓ built in          | DIY similarity threshold              |
| Privacy / runs locally                | depends on infra  | depends on infra    | most controllable                     |

### Recommendation

For forge as described, **graphiti is overkill**. The data is
mostly "I read this page, here's the text" — there's no entity-
relation graph to traverse, no bi-temporal "what did I believe at
time T" pattern to answer. The browser already records timestamps.

**Two reasonable paths:**

1. **mem0** — if you want a library that handles classification +
   dedup + search out of the box. Cheap to write, easy to
   integrate. The graph mode is there if you discover you do want
   entity reasoning later.

2. **Custom thin layer (pgvector + embeddings + sqlite metadata)**
   — if you want maximum control + zero LLM cost at ingest time
   and don't want a library defining your data model. Roughly:

   ```sql
   CREATE TABLE pages (
     id          UUID PRIMARY KEY,
     url         TEXT NOT NULL,
     title       TEXT,
     content     TEXT,
     embedding   vector(1536),
     visited_at  TIMESTAMPTZ,
     tags        TEXT[]
   );
   CREATE INDEX ON pages USING hnsw (embedding vector_cosine_ops);
   ```

   That + a Python service with `add_page(url, content)` /
   `search(query, k=10)` / `purge(before=...)` is maybe 200
   lines and outperforms either library on this specific
   workload.

**Don't use TEMPER as-is for forge data.** TEMPER's graphiti
extractor is tuned for "structured agent observations", not
"web page text dumps". Trying to fit forge's data into TEMPER
would either pollute the graph with noise (every page becomes
entities + edges) or require turning off extraction (which
defeats the point of running graphiti).

**Do consider sharing the memory_blocks primitive across both.**
Identity / preferences are user-level and should be visible to
both forge and smith. TEMPER's blocks layer is small, generic
KV, and works fine as a shared user-state primitive.

---

## When to revisit

This comparison is a snapshot of state in 2026-05. Things that
would change the recommendation:

- mem0 ships strong bi-temporal support → its weak-spot vs
  graphiti shrinks
- graphiti's extraction quality on first-person assertions gets
  fixed upstream → memory_blocks becomes optional, not load-bearing
- A new library combines both well → revisit
- forge's data shape evolves beyond "web pages" into entity-heavy
  domains → graphiti becomes more relevant

For now, the lines are clear: **graphiti for curated structured
agent memory (TEMPER's existing scope), mem0 or a thin custom
layer for high-volume low-structure browser data (forge's
incoming scope)**.
