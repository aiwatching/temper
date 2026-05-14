/**
 * pi extension: injects Smith's system prompt at every turn AND
 * mandatorily pre-fetches relevant memory from TEMPER.
 *
 * Why mandatory pre-fetch (not just an LLM tool):
 *
 *   We initially relied on the model to call `memory_search` itself,
 *   driven by the per-tool description + a strong system prompt. In
 *   practice forti-k2 (and even larger models on short questions)
 *   often skip the tool call and answer from training data —
 *   reproducible miss: user says "my name is Alex", smith writes the
 *   episode correctly, restart, "what's my name?" → "I don't know".
 *
 *   The fix is structural, not prompt-engineering: every user message
 *   triggers a Temper search BEFORE the model runs, and the hits are
 *   pasted into the system prompt as a "Memory recall" section. The
 *   model can't opt out of seeing them.
 *
 * The `memory_search` tool stays exposed so the model can dig deeper
 * when the auto-recall didn't bring enough. `memory_write` is the
 * model's responsibility — writing is always intentional.
 */
import { getConfig } from "../config.js";
import { Temper } from "../temper.js";
import type { EpisodeSummary, SearchHit } from "../temper.js";

// biome-ignore lint: pi.ExtensionAPI types are still moving — see other extensions.
type PiExtensionAPI = any;

const SMITH_BASE_PROMPT = `You are Smith, a personal company-level assistant.

You have a persistent, multi-tenant graph memory in TEMPER. Every
write extracts entities + relations (facts) and stores them with
bi-temporal validity. You can write, search, chain related events,
cluster the graph, and constrain extraction with custom schemas.

═══ Tools ═══

memory_search(query, limit?, as_of?, namespaces?, ...)
    Semantic + graph search (bi-temporal aware) over TEMPER. Each hit
    has { fact, score, valid_at, invalid_at, ... }.

memory_write(content, source_description?, reference_time?, tags?,
             saga?, namespace?)
    Write ONE discrete episode. Paraphrase rather than verbatim
    transcript. \`reference_time\` is when the event actually happened
    (default = now). \`saga\` chains related episodes (one
    conversation / one task).

<server>__<tool>
    Bridged from internal MCP servers (Mantis, GitLab, PMDB, …).
    Use like any other tool.

═══ Auto-retrieved memory (Smith does this FOR you each turn) ═══

Before this turn ran, Smith already searched TEMPER with the user's
message and pasted the top hits below under "Memory recall". TREAT
THAT SECTION AS GROUND TRUTH about this user — their name(s) and
nicknames, preferences, ongoing projects, decisions, shared context.
Quote it (paraphrased) when asked anything personal. NEVER say "I
don't know" if the answer sits in Memory recall.

If Memory recall is empty or doesn't cover the question well, call
memory_search yourself with a more specific query (e.g. when the
user references "as I mentioned", a saga name, or a past time).

═══ Mental model ═══

Episode    raw event you record. Extraction makes Entities + Facts.
Entity     a node (Person, Place, Project, ...).
Fact       an edge between two entities with valid_at / invalid_at.
Saga       named chain of episodes (e.g. one conversation, one task).
Community  cluster of related entities, summarized.
Schema     optional typed contract for an entity kind.

═══ When to WRITE ═══

Call memory_write when the user:
  - states a preference ("I like X over Y")
  - tells you a durable fact about themselves ("my name is …",
    "I'm working on …", "I report to …")
  - tells you what they want to call you (your nickname from them)
  - makes a decision future-you should know about
  - explicitly asks you to remember something
  - hits a milestone worth recalling across sessions

ONE discrete fact per call. Pick tags future-you will search by.

If the user contradicts a stored fact, just write the new state —
TEMPER's bi-temporal model handles invalidation. Don't try to delete
or modify directly.

NEVER write:
  - credentials, tokens, passwords, full credit cards
  - PII the user hasn't consented to storing
  - one-off chitchat with no future value

═══ When to SEARCH explicitly (beyond the auto-recall) ═══

  - the user references past context ("as I mentioned", "last time",
    "remember when …")
  - the user names a saga / project / person not in Memory recall
  - you're about to act on a fact and want to double-check
  - the question asks "what was true at <past time>?" → pass as_of

═══ Namespace shapes ═══

  user:<id>             user's flat namespace, shared across ALL
                        their agents (cross-agent recall)
  agent:<id>/<slug>     one named agent under a user; isolated unless
                        deliberately sharing the slug
  user:me               shortcut for the caller's user namespace
  agent:me/<slug>       shortcut for the caller's agent slug

Default: omit \`namespace\` to use Smith's own scope. Write to
\`user:me\` ONLY when you want the user's OTHER agents to see this
fact too (cross-agent).

═══ What TEMPER does NOT decide — you must ═══

  - Memorability: pick what's worth writing, don't dump transcripts.
  - Secret filtering: strip credentials / PII before write.
  - Saga boundaries: decide when a chain starts / ends.
  - Surfacing: pick the top 1–3 hits, paraphrase, never read raw
    JSON to the user.
  - Disambiguation: in shared namespaces fact text may not name WHO
    said it — keep author context yourself.
  - Conflict policy: when TEMPER's bi-temporal model disagrees with
    an external source of truth, pick a winner per situation.
  - Intent routing: not every question needs memory — decide first.

═══ Rules ═══

  - Terse, action-oriented replies.
  - Paraphrase memory hits; never quote raw JSON.
  - One discrete fact per memory_write.
  - reference_time = when it happened, not when you recorded it.
  - On contradictions, prefer newer \`valid_at\` with \`invalid_at = null\`.
    If both look current, flag the conflict to the user.
`;

const MAX_RECALL_HITS = 5;
const MAX_RECENT_EPISODES = 5;

function formatHits(hits: SearchHit[]): string {
  if (hits.length === 0) return "  (no fact hits)";
  return hits
    .slice(0, MAX_RECALL_HITS * 2)
    .map((h, i) => {
      const fact = h.fact ?? h.name ?? "(no fact)";
      const score = typeof h.score === "number" ? ` (score=${h.score.toFixed(2)})` : "";
      const valid = h.valid_at ? `  valid_at=${h.valid_at}` : "";
      return `  ${i + 1}. ${fact}${score}${valid}`;
    })
    .join("\n");
}

async function fetchRecentEpisodesWithContent(
  t: Temper,
  namespace: string,
  limit: number,
): Promise<Array<{ ep: EpisodeSummary; content: string }>> {
  // Need content (which isn't in the list response), so fetch detail
  // for each — N+1 but small N. The list endpoint is paged by `before`,
  // so we walk the most recent ones.
  const summaries = await t.listEpisodes({ namespace, limit });
  const detailed = await Promise.all(
    summaries.map(async (ep) => {
      try {
        const detail = await t.getEpisode(ep.episode_id);
        return { ep, content: detail.content };
      } catch {
        return { ep, content: "(content fetch failed)" };
      }
    }),
  );
  return detailed;
}

function formatEpisodes(items: Array<{ ep: EpisodeSummary; content: string }>): string {
  if (items.length === 0) return "  (no recent episodes)";
  return items
    .map(({ ep, content }, i) => {
      const when = ep.reference_time ?? ep.created_at;
      const tags = ep.tags?.length ? ` [${ep.tags.join(", ")}]` : "";
      // Trim noisy whitespace, cap length so the prompt doesn't explode.
      const trimmed = content.replace(/\s+/g, " ").trim().slice(0, 240);
      return `  ${i + 1}. (${when}${tags}) ${trimmed}`;
    })
    .join("\n");
}

export function smithPersonalityExtension(pi: PiExtensionAPI): void {
  // Lazy Temper client — one per session lifetime (factory is called
  // once per session, handler is called per agent_start).
  let temper: Temper | null = null;
  const getTemper = (): Temper => {
    if (temper === null) temper = new Temper();
    return temper;
  };

  pi.on(
    "before_agent_start",
    async (event: { prompt: string }): Promise<{ systemPrompt?: string }> => {
      // Auto-search using the user's message as the query. Best-effort:
      // a Temper outage shouldn't break the chat — we still ship the
      // base prompt and let the model answer without memory context.
      let recallBlock = "";
      try {
        const cfg = getConfig();
        const t = getTemper();
        const ownScope = `agent:me/${cfg.smithAgentSlug}`;

        // Search the two relevant scopes SEPARATELY and merge.
        // Temper ranks across all requested namespaces into a single
        // top-N list, so a noisy / high-volume namespace (typically
        // user:me with months of cross-agent context) crowds out a
        // sparse one (this agent's own writes). Two queries → merge
        // → dedup gives Smith's own scope guaranteed airtime.
        //
        // Also pull recent RAW episode content from this agent's
        // scope: Graphiti's fact extraction occasionally flips
        // agency (e.g. "user wants to call me X" → entity summary
        // "user wants to be called X"). Showing both the extracted
        // facts AND the original episode wording lets the LLM
        // ground-truth check before answering.
        const [userHits, agentHits, recentOwn] = await Promise.all([
          t.search({
            query: event.prompt,
            limit: MAX_RECALL_HITS,
            namespaces: ["user:me"],
          }).catch(() => [] as SearchHit[]),
          t.search({
            query: event.prompt,
            limit: MAX_RECALL_HITS,
            namespaces: [ownScope],
          }).catch(() => [] as SearchHit[]),
          fetchRecentEpisodesWithContent(t, ownScope, MAX_RECENT_EPISODES)
            .catch(() => [] as Array<{ ep: EpisodeSummary; content: string }>),
        ]);

        // Dedup hits by fact text — Graphiti often returns both a
        // "fact" hit and an "entity" hit with the same text.
        const seen = new Set<string>();
        const hits: SearchHit[] = [];
        for (const h of [...agentHits, ...userHits]) {
          const key = (h.fact ?? h.name ?? "").trim();
          if (!key || seen.has(key)) continue;
          seen.add(key);
          hits.push(h);
        }

        const factsSection = formatHits(hits);
        const recentSection = formatEpisodes(recentOwn);

        recallBlock =
          "\n═══ Memory recall (auto-retrieved for this turn) ═══\n\n" +
          "EXTRACTED FACTS (semantic search; Graphiti's extraction may\n" +
          "occasionally flip agency or lose nuance — cross-check against\n" +
          "the raw episodes below before answering):\n" +
          factsSection +
          "\n\n" +
          `RECENT EPISODES in your own scope (${ownScope}, raw content):\n` +
          recentSection +
          "\n";

        console.log(
          `[smith] auto-recall: ${hits.length} fact hits + ${recentOwn.length} recent episodes for "${event.prompt.slice(0, 50)}"`,
        );
        for (const [i, h] of hits.entries()) {
          console.log(`  fact ${i + 1}. ${h.fact ?? h.name ?? "(no fact)"}`);
        }
      } catch (e) {
        console.warn(`[smith] auto-recall failed: ${(e as Error).message} — proceeding without it`);
      }
      return { systemPrompt: SMITH_BASE_PROMPT + recallBlock };
    },
  );
}
