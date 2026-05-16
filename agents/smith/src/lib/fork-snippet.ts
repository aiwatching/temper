/**
 * Build the "Branched from main" snippet that's injected into a forked
 * conversation's system prompt at every turn.
 *
 * Snippet is generated ONCE at fork time and persisted in the branch's
 * IndexEntry.forkedFrom.snippet so:
 *   - re-reads are cheap (no jsonl scan per turn)
 *   - edits to the source conv later (e.g. main getting cleared) don't
 *     mutate what the branch sees — it remembers what it was forked from
 *
 * Four range modes (default B):
 *
 *   A — cited reply only
 *       Just the assistant message that was anchored. Use when the
 *       reply is self-contained ("here's the SQL you asked for") and
 *       the branch's first turn will set its own direction.
 *
 *   B — cited reply + the user message that triggered it
 *       One round (user + assistant). The minimum context that
 *       preserves the *why*. Cheap, almost always enough.
 *
 *   C — cited reply + N rounds before and after
 *       Configurable window. Use when the citation is mid-discussion
 *       and the branch needs surrounding context to make sense.
 *
 *   E — cited reply + LLM-generated summary of the whole source
 *       For long sources where you want overall context but not the
 *       raw transcript. Costs one LLM call at fork time; reserved
 *       — engine doesn't currently make the call (TODO).
 */
import { readFileSync, existsSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

export type ForkRange = "A" | "B" | "C" | "E";

export interface SimpleTurn {
  role: "user" | "assistant";
  text: string;        // concatenated text content (skips thinking + raw tool blocks)
  ts: string;          // ISO
}

function jsonlPath(convId: string): string {
  return resolvePath(process.cwd(), ".data", "smith-sessions", `${convId}.jsonl`);
}

/** Parse a conversation's jsonl into the simple user/assistant turn
 *  sequence. Filters out thinking blocks (LLM internal), tool calls
 *  (shown only as "[called X]" markers), and tool results (the
 *  assistant integrates them in its next text turn). */
export function readConversationTurns(convId: string): SimpleTurn[] {
  const path = jsonlPath(convId);
  if (!existsSync(path)) return [];
  const text = readFileSync(path, "utf8");
  const out: SimpleTurn[] = [];
  for (const raw of text.split("\n")) {
    if (!raw.trim()) continue;
    let line: { type?: string; timestamp?: string; message?: { role?: string; content?: Array<{ type?: string; text?: string; name?: string }> } };
    try { line = JSON.parse(raw); } catch { continue; }
    if (line.type !== "message" || !line.message) continue;
    const role = line.message.role;
    if (role !== "user" && role !== "assistant") continue;
    const parts: string[] = [];
    for (const c of line.message.content ?? []) {
      if (c.type === "text" && c.text) parts.push(c.text);
      else if (c.type === "toolCall" && c.name) parts.push(`[called ${c.name}]`);
      // thinking blocks intentionally omitted — internal scratchpad
    }
    if (parts.length === 0) continue;
    out.push({
      role,
      text: parts.join("\n").trim(),
      ts: line.timestamp ?? "",
    });
  }
  return out;
}

const HEADER = (sourceConv: string, ts: string, range: ForkRange, hint: string) =>
  `\n═══ Branched from ${sourceConv} on ${ts.slice(0, 19).replace("T", " ")} (range=${range}) ═══\n\n` +
  `${hint}\n\n` +
  `This branch is independent of ${sourceConv} — your changes here\n` +
  `won't affect ${sourceConv}, and ${sourceConv}'s later updates won't\n` +
  `show up here.\n`;

const FOOTER = "";

function renderTurn(t: SimpleTurn, idx: number, anchor: number): string {
  const who = t.role === "user" ? "USER" : "SMITH";
  const star = idx === anchor ? "  ⟵ anchor" : "";
  const time = t.ts ? `@ ${t.ts.slice(11, 19)}` : "";
  return `[${who} ${time}]${star}\n  ${t.text.replace(/\n/g, "\n  ")}`;
}

/** Build the snippet text for a fork. */
export function buildForkSnippet(args: {
  sourceConv: string;
  turns: SimpleTurn[];          // result of readConversationTurns
  anchor_turn: number;          // index of the cited assistant reply in `turns`
  range: ForkRange;
  n?: number;                   // for range=C
}): string {
  const { sourceConv, turns, anchor_turn, range, n } = args;
  const anchorTurn = turns[anchor_turn];
  if (anchor_turn < 0 || anchor_turn >= turns.length || anchorTurn === undefined) {
    return HEADER(sourceConv, new Date().toISOString(), range,
      "(anchor turn out of range — fork created with empty context)") + FOOTER;
  }

  let slice: SimpleTurn[];
  switch (range) {
    case "A":
      slice = [anchorTurn];
      break;
    case "B": {
      // The user message that triggered this reply is the most recent
      // user turn at index < anchor. Usually anchor-1, but skip
      // toolResult-style turns just in case.
      let userIdx = anchor_turn - 1;
      while (userIdx >= 0 && turns[userIdx]?.role !== "user") userIdx--;
      const userTurn = userIdx >= 0 ? turns[userIdx] : undefined;
      slice = userTurn ? [userTurn, anchorTurn] : [anchorTurn];
      break;
    }
    case "C": {
      const window = Math.max(1, n ?? 2);
      const start = Math.max(0, anchor_turn - window * 2);
      const end = Math.min(turns.length, anchor_turn + window * 2 + 1);
      slice = turns.slice(start, end);
      break;
    }
    case "E":
      // LLM summary not implemented — fall back to B so the fork is
      // at least usable, with a note explaining the gap.
      return (
        HEADER(sourceConv, new Date().toISOString(), range,
          "(range=E is reserved: LLM summary not implemented yet — " +
          "showing range=B content as fallback)") +
        renderSlice(turns, anchor_turn, "B") +
        FOOTER
      );
  }

  return HEADER(sourceConv, new Date().toISOString(), range,
    sliceHint(range, n)) + renderTurns(slice, turns, anchor_turn) + FOOTER;
}

function sliceHint(range: ForkRange, n?: number): string {
  switch (range) {
    case "A": return "Anchored reply only — the branch starts fresh from this point.";
    case "B": return "Anchored reply + the user message that triggered it.";
    case "C": return `Anchored reply ± ${n ?? 2} rounds of surrounding context.`;
    case "E": return "Anchored reply + LLM-summarized source (reserved).";
  }
}

function renderTurns(slice: SimpleTurn[], allTurns: SimpleTurn[], anchorIdxInAll: number): string {
  return slice.map((t, _) => {
    const absoluteIdx = allTurns.indexOf(t);
    return renderTurn(t, absoluteIdx, anchorIdxInAll);
  }).join("\n\n");
}

function renderSlice(turns: SimpleTurn[], anchorIdx: number, range: ForkRange): string {
  // Used by the E fallback path.
  void range;
  const anchor = turns[anchorIdx];
  if (!anchor) return "";
  let userIdx = anchorIdx - 1;
  while (userIdx >= 0 && turns[userIdx]?.role !== "user") userIdx--;
  const userTurn = userIdx >= 0 ? turns[userIdx] : undefined;
  const slice: SimpleTurn[] = userTurn ? [userTurn, anchor] : [anchor];
  return renderTurns(slice, turns, anchorIdx);
}
