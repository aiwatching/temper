# Vision — memory brain + reference shelf

Captured 2026-05-13 during a discussion about whether to fold an
Obsidian-style document model into Memory Service. Outcome: not now.
This doc is the rationale + the four options we considered, so the
decision is recoverable when we come back to it.

## Two complementary mental models

### Temper (graph + episodes) ≈ human brain

- **Input is passive.** Episodes stream in from whatever agent is
  talking to the service; nothing is curated. You write what happened,
  the system decides what to keep.
- **Links are emergent.** Entity dedup, temporal `valid_at`/`invalid_at`,
  community clustering — all derived by the LLM extractor, not the
  caller.
- **Recall is associative.** You ask "who's Jerry's teacher" and the
  system surfaces facts that were never explicitly indexed under that
  question. Fuzzy, accumulative, biased toward whatever is connected.
- **Time-aware.** `as_of` queries return what you "believed" on a
  specific date. Old beliefs aren't deleted, just shelved.

### Obsidian (markdown vault) ≈ a book / paper / notes

- **Input is deliberate.** A human (or rarely, an agent) sat down and
  wrote the note, chose the title, added wikilinks.
- **Links are explicit.** `[[wiki/python-loops]]` means exactly that
  page; backlinks are computed but the forward link is intentional.
- **Recall is deliberate.** You know what file you want to open, you
  open it, you read the whole thing.
- **Stable.** A note has one current state. Edit it and the previous
  version is just gone (or in git).

These don't compete — they complement:

> The brain remembers "I talked to Sarah on Tuesday about Python loops,"
> which makes me decide to go open [[wiki/python-loops]] and re-read it.
> Reading the note becomes a new episode entering the brain.

## Where this leaves Memory Service today

Temper is well-positioned for the brain role. Open question: do we add
the bookshelf next to it, or keep the bookshelf separate (Obsidian
proper, or some other markdown store)?

## Four options we considered

### Option 1 — graph unchanged; add Documents as a separate primitive
- New `Document` table, peer to `EpisodeMetadata`.
- Not parsed for entities/facts, not in the graph.
- `/v1/search` returns "related notes: [path1, path2]" alongside facts;
  agent decides whether to fetch them.
- Obsidian is, literally, an "external shelf."

### Option 2 — graph unchanged; documents are mostly for the human
- You maintain a real Obsidian vault locally.
- Memory Service occasionally pulls excerpts to write as episodes
  (manual or semi-automatic).
- Agent doesn't know documents exist; only sees the graph.
- Documents are a personal workflow, not part of the agent API.

### Option 3 — shared namespace, parallel storage
- Same `user:`/`group:`/`org:` matrix covers both primitives.
- `/v1/search?include=episodes,documents` returns blended results.
- Backlinks are a Document concept, not a graph concept; the two
  indexes stay separate underneath.

### Option 4 — graph is the center; documents are derived long-form
- Documents are mostly auto-generated: e.g. `build_communities` also
  emits a markdown summary per Community ("this week about Sarah:
  …"); agents (and humans) can read those.
- Hand-written documents from outside coexist with derived ones in the
  same namespace.

Closest to the "翻书" metaphor: **Option 1 or 2**. Agent has a brain
(graph) and a bookshelf (documents); they meet only at the moment the
agent decides to fetch.

## Decision: defer

For now, finish exercising the brain layer. The graph + episode side
already has 30+ endpoints across writes, search, communities, sagas,
schemas, async, reindex, cypher, etc. (see `docs/api-guide.md` and
recent git log). Until we've actually used that under real workloads
and identified where the missing "looked-up reference" hurts, adding
a Document primitive risks building the wrong shape.

When we come back to this: re-read the four options above and the
"emergent vs deliberate" framing — that's the lens that will pick the
right shape.
