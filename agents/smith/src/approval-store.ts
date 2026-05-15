/**
 * Approval store for the destructive-tool gate.
 *
 * Smith's `tool_call` hook (see src/extensions/approval-gate.ts)
 * checks every tool call against this store before letting it run.
 * Dangerous tools block + emit a `pending` event; the UI surfaces
 * an Approve / Deny button; on Approve, POST /approve writes into
 * the store; the LLM retries on the next turn and the hook consumes
 * the approval.
 *
 * State is in-memory only — restart wipes pending + approved. That's
 * intentional: approvals are short-lived (one-shot, tied to a specific
 * (conversation, tool, args) triple). Crossing a restart deserves a
 * fresh confirm.
 */
import { EventEmitter } from "node:events";
import { createHash } from "node:crypto";

/** Stable JSON for hashing (sorts object keys recursively). */
function stableStringify(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  const obj = value as Record<string, unknown>;
  const keys = Object.keys(obj).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${stableStringify(obj[k])}`).join(",")}}`;
}

export function argsHash(args: unknown): string {
  return createHash("sha256").update(stableStringify(args)).digest("hex").slice(0, 16);
}

function approvalKey(toolName: string, hash: string): string {
  return `${toolName}:${hash}`;
}

export interface PendingApproval {
  conversationId: string;
  toolCallId: string;
  toolName: string;
  input: unknown;
  argsHash: string;
}

class ApprovalStore {
  /** conversationId → Set<approvalKey> */
  private approved = new Map<string, Set<string>>();
  /** conversationId → most-recent pending payload (for UI re-render on reconnect). */
  private pendingByConv = new Map<string, PendingApproval>();
  /** Single bus — server.ts subscribes per SSE stream. */
  readonly events = new EventEmitter();

  /** Called by /approve. */
  approve(conversationId: string, toolName: string, hash: string): void {
    if (!this.approved.has(conversationId)) {
      this.approved.set(conversationId, new Set());
    }
    this.approved.get(conversationId)!.add(approvalKey(toolName, hash));
    this.pendingByConv.delete(conversationId);
    this.events.emit("approved", { conversationId, toolName, argsHash: hash });
  }

  /** Called by /deny — drop the pending and notify so UI clears. */
  deny(conversationId: string, toolName: string, hash: string): void {
    this.pendingByConv.delete(conversationId);
    this.events.emit("denied", { conversationId, toolName, argsHash: hash });
  }

  /**
   * Called from the tool_call hook. Removes the entry if found (one-shot)
   * and returns true; the same call requires re-approval if the LLM
   * tries it twice.
   */
  consume(conversationId: string, toolName: string, input: unknown): boolean {
    const hash = argsHash(input);
    const set = this.approved.get(conversationId);
    if (!set) return false;
    const key = approvalKey(toolName, hash);
    if (!set.has(key)) return false;
    set.delete(key);
    return true;
  }

  /** Mark a tool call as awaiting approval + notify subscribers. */
  markPending(payload: PendingApproval): void {
    this.pendingByConv.set(payload.conversationId, payload);
    this.events.emit("pending", payload);
  }

  /** UI can re-fetch on reconnect to show buttons that were missed. */
  getPending(conversationId: string): PendingApproval | undefined {
    return this.pendingByConv.get(conversationId);
  }
}

export const approvalStore = new ApprovalStore();

/**
 * Heuristic: is this tool name dangerous enough to require user
 * approval?
 *
 *   - Smith's own memory tools are NEVER gated. They're the agent's
 *     scratch space; gating would make it annoying to use.
 *   - Any read-shaped verb (get / list / search / read / show / view /
 *     find / describe / fetch / status) is safe.
 *   - Mutation verbs (close / merge / delete / send / update / create /
 *     remove / assign / approve / push / deploy / run / exec / start /
 *     stop / restart / publish / archive) are dangerous.
 *   - Anything not matching either pattern defaults to safe (so adding
 *     a new tool doesn't unexpectedly require approval). Tools with a
 *     genuinely ambiguous name should explicitly opt-in via the
 *     forceDangerous override below.
 */
const SAFE_RE =
  /(?:^|_)(?:get|list|search|read|show|view|find|describe|fetch|status|count|info|history)(?:$|_)/i;

const DANGEROUS_RE =
  /(?:^|_)(?:close|merge|delete|send|update|create|remove|assign|approve|push|deploy|run|exec|execute|start|stop|restart|publish|archive|edit|patch|put|post|set|reset|apply)(?:$|_)/i;

// Explicit safe list for Smith's own scratch-space tools. We used to
// blanket-allow anything starting with `memory_`, but adding
// memory_consolidate_apply (which IS destructive) exposed the gap.
// Whitelist by name instead.
const forceSafe = new Set<string>([
  "memory_search",
  "memory_write",
  "memory_consolidate",        // plan only — read-only
]);
const forceDangerous = new Set<string>([
  "memory_consolidate_apply",  // belt + suspenders alongside the *_apply regex
  "memory_correct_apply",      // invalidates a fact + writes new episode + resummarizes
]);

export function isDangerous(toolName: string): boolean {
  if (forceSafe.has(toolName)) return false;
  if (forceDangerous.has(toolName)) return true;
  if (SAFE_RE.test(toolName)) return false;
  return DANGEROUS_RE.test(toolName);
}

/** Test hook — used so smoke tests can force a known tool through the gate. */
export function _testForceDangerous(name: string): void {
  forceDangerous.add(name);
}
