/**
 * pi extension: customise pi's automatic context compaction.
 *
 * pi's default behaviour: when the running context creeps past a
 * fraction of the model's window, pi summarises the older turns into
 * a single "compaction" entry and replays just that + the recent
 * turns to the model. Cheap and works, but two practical losses on
 * an agent like Smith:
 *
 *   1. Durable facts the user told us, and the memory_write tool
 *      calls that recorded them, can get smudged into a generic
 *      "the user discussed X" line. Then the model can't retrieve
 *      them later because they're no longer in the conversation.
 *
 *   2. The compaction summary lives ONLY in the JSONL file. If the
 *      user starts a new conversation tomorrow, search across
 *      Temper won't find anything that happened in turns 1-50 of
 *      yesterday's thread.
 *
 * Two hooks fix that:
 *
 *   - `session_before_compact`: mutate the event's `customInstructions`
 *     in place to tell the summary LLM what to preserve verbatim.
 *     (pi documents in-place mutation as the supported pattern for
 *     event fields; cf. ToolCallEvent.input.)
 *
 *   - `session_compact`: after pi finishes summarising, write the
 *     summary as ONE Temper episode in the agent's own scope tagged
 *     `compaction-summary`. Future cross-thread auto-recall picks
 *     it up like any other memory.
 */
import { Temper } from "../temper.js";

// biome-ignore lint: pi.ExtensionAPI types are still moving — see other extensions.
type PiExtensionAPI = any;

const PRESERVATION_RULES = `

IMPORTANT (Smith compaction policy): When summarising, PRESERVE VERBATIM:

  - Any memory_write tool calls and their exact \`content\` argument —
    these are durable facts the user wanted recorded; if the model
    can't see them after compaction, they're effectively lost.
  - Tool call decisions: which tool was called with what args, the
    success/failure outcome, and any data the model relied on from
    the result. Strip noisy details (full diffs, large search hit
    lists), but keep the WHAT and WHY.
  - User-stated identity facts: name, role, team, current project,
    explicit preferences ("I like X over Y"), and any nicknames or
    aliases.
  - The current state of any ongoing saga / task: where the user is
    in a multi-turn workflow, what blockers exist, what's the next
    action.

DROP / COMPRESS:

  - Greetings, acknowledgements, small talk.
  - Repeated tool retries with the same outcome.
  - Long verbatim paste-backs from tools (summarise instead).
  - Smith's own filler ("Let me think…", "I'll help you with that").
`;

export function compactionPolicyExtension(
  pi: PiExtensionAPI,
  conversationId: string,
): void {
  // ---- Before: nudge the summary LLM toward preservation ----
  //
  // SessionBeforeCompactResult doesn't expose customInstructions as a
  // return field, but the event object is mutable (pi's documented
  // pattern — see ToolCallEvent.input handling). Append our rules so
  // we don't trample any prior extensions' instructions.
  pi.on(
    "session_before_compact",
    (event: { customInstructions?: string; preparation?: { tokensBefore?: number } }) => {
      const before = event.customInstructions ?? "";
      event.customInstructions = before + PRESERVATION_RULES;
      const tokens = event.preparation?.tokensBefore ?? 0;
      console.log(
        `[smith] compaction starting: conv=${conversationId} compacting ~${tokens} tokens`,
      );
    },
  );

  // ---- After: persist the summary to Temper ----
  //
  // `fromExtension: true` means another extension generated the
  // CompactionEntry (e.g. an artifact-aware compactor) and we'd
  // double-archive — skip to be safe.
  pi.on(
    "session_compact",
    async (event: {
      compactionEntry: { summary: string; tokensBefore: number };
      fromExtension?: boolean;
    }) => {
      if (event.fromExtension) return;
      const { summary, tokensBefore } = event.compactionEntry;
      const content =
        `[Compaction summary from conversation ${conversationId}, ` +
        `${tokensBefore} tokens compacted]\n\n${summary}`;
      try {
        const t = new Temper();
        await t.write({
          content,
          sourceType: "text",
          sourceDescription: "smith compaction archive",
          tags: ["compaction-summary"],
        });
        console.log(
          `[smith] compaction summary archived to Temper (conv=${conversationId}, ${tokensBefore} tokens)`,
        );
      } catch (e) {
        // Non-fatal — pi's compaction has already replaced the
        // in-context history. The summary survives in the JSONL.
        // The cross-thread recall just loses this snapshot.
        console.warn(
          `[smith] compaction summary archive failed: ${(e as Error).message} — ` +
          `the summary stays in the local JSONL but won't be cross-thread recallable.`,
        );
      }
    },
  );
}
