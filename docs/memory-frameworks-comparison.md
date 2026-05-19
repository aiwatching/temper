# Memory frameworks — mem0 vs graphiti (deep dive)

Two redos of this document:

> v1 (earlier 2026-05-16): misread the new agent's use case as
> "indexing web page dumps". Recommended mem0 / pgvector for it.
> THAT WAS WRONG.
>
> v2 (this doc): the chrome-extension agent has the **same use
> case as Smith** — enterprise employee memory (decisions, people,
> projects, tasks). It just delivers as a browser extension
> instead of an HTTP service, and ingests browser-native data
> (Gmail, Calendar, internal pages) instead of MCP. The memory
> shape doesn't change. graphiti is still the right call.

Below: the deep comparison, why graphiti wins for this shape, and
exactly where mem0 would lose.

---

## 1. The use case (corrected)

The agent is a personal **enterprise-employee memory** for one
user. Concretely it needs to answer:

| Query                                      | What's needed under the hood |
|---|---|
| "Who's working on the auth refactor"       | Person → WORKING_ON → Project, graph walk |
| "What did we decide about JWT vs session"  | Decision episode, fact edge with valid_at |
| "Has Sarah replied to my Friday thread"    | Entity-filter + temporal query |
| "What's the status of bug 12483"           | Project state with bi-temporal updates |
| "Who reports to David"                     | Person → REPORTS_TO → Person, graph walk |
| "When did Bob switch from team A to B"     | Bi-temporal: A.invalid_at = T, B.valid_at = T |
| "Show me everything about Project-X"       | Entity-centric BFS, multiple hops |
| "What was true about X last quarter"       | as_of = <past date>, return facts valid then |

This is **all entity-relation reasoning over time**. None of it is
"vector search through a pile of blobs". The use case has not
changed just because the host moved from a standalone server to a
chrome extension.

What HAS changed about the delivery:

- **Ingest source**: was MCP servers (Mantis, GitLab, Outlook).
  Now Chrome (Gmail API, Calendar API, page DOM scraping for
  internal Confluence / Jira / etc).
- **Auth**: was per-MCP-server (painful, unstable). Now per-Google /
  per-OAuth at user level (browser handles it).
- **Network topology**: extension → TEMPER over HTTP. No MCP
  middleware.
- **Loop placement**: TBD — agent loop in extension JS or
  delegated to cloud LLM service.

None of that changes the memory backend choice.

---

## 2. Deep comparison

### 2.1 The fundamental difference: what is "a memory"

**mem0** treats memory as **classified text**:

```
input:  "Sarah said in standup that Bob needs to ship auth by Friday"
↓ LLM classify
memories: [
  "Sarah said Bob needs to ship auth by Friday",
  "Auth deadline is Friday",
  "Bob is working on auth"
]
↓ embed each, store in pgvector with metadata { user_id, ts }
```

3 blobs, each with an embedding + timestamp + user. To find "what
auth deadline?" → cosine similarity over the query. The connection
"Bob → auth → Friday" is **implicit in the text** of memory blob #1.

**graphiti** treats memory as **entities + relations + temporal
facts**:

```
input:  "Sarah said in standup that Bob needs to ship auth by Friday"
↓ LLM extract
entities: Sarah(Person), Bob(Person), AuthProject(Project),
           StandupMeeting(Event)
edges:    Sarah ATTENDED StandupMeeting (valid_at=now, source=ep1)
          Bob OWNS AuthProject (valid_at=now, source=ep1)
          AuthProject HAS_DEADLINE "Friday" (valid_at=now, source=ep1)
↓ persist as graph nodes + edges with bi-temporal validity
```

Same input, structurally decomposed. "What auth deadline?" → look
up `AuthProject` node → follow `HAS_DEADLINE` edge → "Friday". No
similarity search needed; the answer is a graph traversal.

When 3 weeks later Bob hands auth to Alice:

```
input:  "Bob handed auth project to Alice today"

mem0 effect:
  → new memory blob "Bob handed auth to Alice"
  → search for "auth owner" gets BOTH old blob (Bob) and new blob
    (Alice). Which wins? The reranker hopefully picks the newer
    one. Hopefully.

graphiti effect:
  → Bob OWNS AuthProject: invalid_at set to today
  → Alice OWNS AuthProject: valid_at = today, invalid_at = NULL
  → search "auth owner" with as_of=now → Alice. as_of=2 weeks ago
    → Bob. The conflict is structurally resolved, both queryable.
```

This is the load-bearing difference for an enterprise-memory agent.
Decisions, project ownership, team membership, meeting attendance —
**all of these change over time**. mem0's "newer memory wins via
rerank" pattern works ok for casual use; it does NOT work when the
user asks "who owned auth in Q2?" and the agent confidently answers
"Alice" because that's the most-recent embedded blob.

### 2.2 Search precision on entity questions

Same scenario: user has 6 months of meeting notes. Asks "who's on
the auth team?"

**mem0**:
- Query embeds to a vector
- Top-K vector search across all stored memory blobs
- Reranker reorders by relevance
- Returns 5-10 blobs that MENTION auth + team
- Agent reads them, infers who's on the team

This works when the team is mentioned together explicitly. Breaks
when team membership is scattered across N meetings: "Bob owns the
auth refactor" + "Sarah reviewed the auth design" + "Alice on the
auth standup". No single blob says "the auth team is [Bob, Sarah,
Alice]". Vector search won't aggregate.

**graphiti**:
- Query parses to entity hint "auth team" or AuthProject
- BFS from AuthProject: find all Person nodes with edges back
  (OWNS, REVIEWED, ATTENDED_STANDUP_FOR, etc.)
- Filter to currently-valid edges
- Return: [Bob, Sarah, Alice]

The graph traversal *aggregates* by structure, not by collocation
in text. This is the use case graphiti exists to solve.

### 2.3 Querying first-person assertions ("how should I address you")

**Both libraries fail at this differently**, which is why TEMPER
added `memory_blocks` as a third primitive.

- mem0: classifier turns "call me X" into a memory blob.
  Subsequent assertion "no wait, call me Y" creates blob #2.
  Vector search for "how to address user" returns both; the LLM
  agent has to pick. Sometimes picks wrong.
- graphiti: extraction turns "call me X" into an entity-summary
  update. Pronoun-flip bug: extractor occasionally renders the
  summary as "X wants to be called Heizai" (subject flip). The
  user reported this multiple times; we built memory_blocks to
  bypass extraction for this class of fact.

`memory_blocks` is a flat JSONB KV store. The agent writes
`preferences.nickname_for_user = "陛下"` directly, no extraction.
This sits alongside graphiti, not instead of it. Same as
TEMPER does today.

A chrome-extension agent would use blocks the same way for
identity / preferences / current focus. Cross-agent (forge +
smith eventually both use it).

### 2.4 Cost analysis at enterprise scale

Assume one user generates these episodes/day:

- 30 emails worth indexing (subject, sender, gist)
- 5 calendar events
- 20 Confluence/Jira page-reads worth indexing (titles + summaries)
- 10 explicit "remember this" agent interactions

Total: ~65 episodes/day, ~24k/year per user.

| Library     | LLM cost/episode | 24k episodes cost | Search cost |
|---|---|---|---|
| graphiti    | 3 calls × ~1500 input + ~500 output tokens at GPT-4o-mini = ~$0.006 | ~$144/year/user | ~$0.001/search × 200 searches/day ≈ $73/year |
| mem0        | 1 call × ~1000 input + ~200 output tokens = ~$0.001 | ~$24/year/user | ~$0.0002/search × 200 ≈ $14/year |
| Custom thin | 0 LLM (just embed) = ~$0.00002 | ~$0.50/year/user | embed-only ≈ $3/year |

graphiti is **~5× more expensive than mem0, ~250× more than thin**.

But: for enterprise SaaS pricing per user, ~$200/year on memory
infra is rounding error against the underlying salary of the
employee. The decision is "does it answer the questions correctly",
not "is it free". If graphiti answers "who owned auth in Q2"
correctly and mem0 doesn't, the cost differential is a non-issue.

### 2.5 Infrastructure footprint

| | graphiti | mem0 |
|---|---|---|
| Required services | Postgres + FalkorDB (or Neo4j) | Postgres + pgvector ext |
| New deploy unit | FalkorDB process (Redis protocol, small) | none (postgres extension) |
| Backup story | dump postgres + falkor RDB | dump postgres |
| Multi-tenant isolation | group_id partition | user_id row filter |
| Local-dev story | docker compose up falkor | nothing extra |

graphiti has one more process. It's small (~50MB RAM steady, RDB
snapshots like Redis). For an enterprise deploy this is
unremarkable. For a single-user-extension model where the backend
is co-located, also fine — FalkorDB starts in seconds.

### 2.6 What graphiti gets you that's hard to retrofit

- **Audit trail**: every fact ever asserted is queryable with its
  valid_at + invalid_at. If a compliance officer asks "what
  ownership records existed on date X", graphiti just answers.
  Retrofitting bi-temporal into mem0 requires reimplementing the
  data model.
- **Entity-typed schemas**: you can declare `Person` has required
  fields (email, team, role); extraction conforms. mem0's
  classifier is free-form.
- **Communities**: graphiti's `build_communities` clusters related
  entities into LLM-summarized groups, dense recall hits. mem0
  doesn't have this.
- **Cross-document reasoning**: BFS from an entity reaches facts
  across N source documents at once. mem0's vector search has no
  notion of "follow this edge".

### 2.7 What mem0 gets you that graphiti makes hard

- **Fast bulk ingest**: feeding 10 years of email history is
  practical with mem0 (~$240 at the numbers above). With graphiti
  it's ~$1400 in extraction.
- **Simpler debugging**: a memory blob's content is the source of
  truth. With graphiti you debug an extractor chain that may have
  decided your fact wasn't a fact, or flipped the pronoun, or
  collapsed two entities.
- **Smaller surface to learn**: 4 verbs, done.
- **More production examples in the wild**: more code on GitHub,
  more blog posts on integration patterns.

---

## 3. TEMPER's already-paid investment

TEMPER currently has:

- graphiti wrapped via `core/memory.py` with bi-temporal search,
  reranker controls, namespace partition
- `memory_blocks` primitive for first-person assertions (the
  pronoun-flip mitigation)
- Typed memory layer (`/v1/memory/tasks`, `/focus`, `/preferences`,
  `/events`, `/turn_context`) on top of both
- The "extraction quality" debugging history (see
  `docs/extraction-quality-2026-05.md`) — we've tuned this
- Multi-user namespace + API key auth + admin import flow

Switching to mem0 means **throwing away all of that** and
re-debugging extraction quality on a different stack. The cost of
the switch is months, not weeks. Unless the destination is clearly
better, the switch is bad.

For this use case, mem0 is NOT clearly better. It's clearly
**cheaper** but on the dimensions that matter for enterprise
memory (entity reasoning + bi-temporal correctness), it's worse.

---

## 4. The "but my extension can't run FalkorDB" question

The chrome-extension shape might suggest "everything has to fit
in the browser". It doesn't. The extension is the **client**;
TEMPER stays where it is (cloud or self-hosted box).

```
┌────────────────────┐         ┌─────────────────────────┐
│ Chrome extension   │  HTTPS  │ TEMPER service          │
│  - DOM scrape     ─┼────────►│  - /v1/episodes  (write)│
│  - Gmail API call  │         │  - /v1/search    (read) │
│  - Cal API call    │         │  - /v1/memory/*  (typed)│
│  - LLM agent loop  │◄────────┼─────────────────────────┤
│  - chat UI         │         │  - Postgres             │
└────────────────────┘         │  - FalkorDB             │
                               │  - graphiti             │
                               └─────────────────────────┘
```

The extension does NOT need FalkorDB. It needs TEMPER's HTTP API.
The shift from MCP to extension doesn't change anything about
TEMPER's side of the wire.

---

## 5. Recommendation (final)

**Stay on graphiti via TEMPER.** Reasons in priority order:

1. **The data shape is entity-relation-heavy across time** — exactly
   what graphiti is built for. mem0's blob-classifier model loses
   the structure that makes "who owns auth", "what changed when",
   "show me everything about Sarah" answerable correctly.

2. **TEMPER is already wrapped, debugged, and operating** —
   throwing away that investment requires the destination to be
   substantially better. mem0 is cheaper-not-better for this shape.

3. **The cost difference is rounding error at enterprise scale** —
   $144/year/user vs $24/year vs salary is invisible.

4. **memory_blocks pattern is portable** — the second primitive
   we built (for the pronoun-flip mitigation) plugs into either
   library. Not a graphiti-lock-in concern.

5. **Bi-temporal queries become real demands** — "what did we
   decide last quarter" is the kind of question enterprise users
   ask weekly. mem0 has no native answer.

What TO do differently from the Smith setup:

- **Reuse the typed memory layer** (`task_add`, `set_focus`,
  `note_event`, etc.) — agent-side wrappers stay the same
  regardless of host.
- **Re-think ingest source patterns** — Gmail/Cal are
  structured-event sources, much cleaner than DOM scraping.
  Build typed import paths:
    * `import_gmail_thread(thread_id)` →
      multiple episodes (one per substantive reply)
    * `import_cal_event(event_id)` →
      one episode with attendees as entities
    * `import_doc(url, content)` →
      one episode tagged source=confluence/jira/etc.
- **OAuth handling lives in the extension** — Chrome's
  identity API handles Google OAuth cleanly. No MCP middleware
  needed.

What NOT to do:

- Don't replicate TEMPER inside the extension. It's a service.
- Don't try to wedge browser-history page-text dumps into
  graphiti. Filter to "interesting" pages first (the user
  explicitly bookmarked / explicit ask / time-spent threshold).
  Random page text is noise that costs extraction LLM calls.
- Don't fork TEMPER for the extension client. Use the HTTP API.
  If the extension wants offline cache, that's a thin layer on
  top — not a fork.

---

## 6. Where this could flip later

Signals that would change the recommendation:

- mem0 ships proper bi-temporal support → its biggest weakness
  for this use case disappears. Still has the entity-aggregation
  gap.
- graphiti's extractor breaks on a new class of input we haven't
  hit yet → forces another primitive (a la memory_blocks). If
  this happens for a 3rd class, time to question the stack.
- A new library combines vector + graph + bi-temporal cleanly →
  evaluate.
- The agent loop moves to fully local (LLM in browser) → ingest
  cost per LLM call goes to ~zero, mem0 vs graphiti cost gap
  disappears entirely. graphiti still wins on data shape.

For now: **graphiti via TEMPER, no change in backend for the
extension agent**.
