/**
 * pi extension: gate dangerous tool calls behind explicit user approval.
 *
 * Wired in session-manager.ts with the current conversationId captured
 * in closure — pi events don't carry the conversationId themselves, so
 * we plumb it in.
 *
 * Behaviour:
 *   - Smith's own memory_search / memory_write: always allowed.
 *   - read-shaped verbs (get / list / search / ...): always allowed.
 *   - mutation verbs (close / merge / delete / send / ...): BLOCKED on
 *     first sight; the store emits a `pending` event the SSE stream
 *     forwards to the UI; user clicks Approve; /approve writes into
 *     the store; LLM retries on the next turn; the hook consumes the
 *     approval and lets it through.
 *
 * Audit trail: every approved + executed dangerous tool writes a row
 * to `.data/audit.log` (jsonl). CC9 in docs/roadmap.md.
 */
import { appendFileSync, mkdirSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

import { approvalStore, argsHash, isDangerous } from "../approval-store.js";

// biome-ignore lint: pi.ExtensionAPI types are still moving — see other extensions.
type PiExtensionAPI = any;

const AUDIT_FILE = resolvePath(process.cwd(), ".data", "audit.log");

function appendAudit(row: Record<string, unknown>): void {
  try {
    mkdirSync(resolvePath(AUDIT_FILE, ".."), { recursive: true });
    appendFileSync(AUDIT_FILE, JSON.stringify({ ...row, when: new Date().toISOString() }) + "\n");
  } catch (e) {
    console.warn(`[smith] audit log write failed: ${(e as Error).message}`);
  }
}

export function approvalGateExtension(pi: PiExtensionAPI, conversationId: string): void {
  pi.on("tool_call", (event: { toolCallId: string; toolName: string; input: unknown }) => {
    const { toolName, toolCallId, input } = event;
    if (!isDangerous(toolName)) return;

    if (approvalStore.consume(conversationId, toolName, input)) {
      console.log(
        `[smith] approval consumed: tool=${toolName} conv=${conversationId} id=${toolCallId}`,
      );
      appendAudit({
        kind: "tool_approved_executed",
        conversationId,
        toolName,
        toolCallId,
        argsHash: argsHash(input),
      });
      return; // unblocked
    }

    // First sighting — block + notify the UI.
    const hash = argsHash(input);
    approvalStore.markPending({
      conversationId,
      toolCallId,
      toolName,
      input,
      argsHash: hash,
    });
    appendAudit({
      kind: "tool_blocked_pending_approval",
      conversationId,
      toolName,
      toolCallId,
      argsHash: hash,
    });
    return {
      block: true,
      reason:
        `BLOCKED: '${toolName}' requires user approval. Smith has surfaced an ` +
        `Approve/Deny button to the user. Tell the user briefly what you wanted ` +
        `to do and stop — do NOT retry without their go-ahead.`,
    };
  });

  // After tool execution lands, audit the outcome (success vs error).
  pi.on(
    "tool_execution_end",
    (e: { toolCallId: string; toolName: string; isError: boolean }) => {
      if (!isDangerous(e.toolName)) return;
      appendAudit({
        kind: "tool_completed",
        conversationId,
        toolName: e.toolName,
        toolCallId: e.toolCallId,
        isError: !!e.isError,
      });
    },
  );
}
