/**
 * pi extension: conversation waiting-flag tools.
 *
 * A conv that's blocked on an external system (CI pipeline, push
 * notification, code review, human reply) should not look like a
 * fresh "active" conv in the /tasks view — it should sit in the
 * "等待中" column with the external label visible. The model is the
 * one in the best position to know when it's stuck: when it has
 * fired the right action and is now waiting for someone / something
 * else to respond.
 *
 * Two tools:
 *   - set_waiting(external, note?) — mark the CURRENT conv as
 *     blocked. Stamped with `since=now` if not already waiting.
 *   - clear_waiting() — unmark; call when the blocker resolves OR
 *     when the conv resumes for unrelated reasons.
 *
 * Both tools target the conversationId captured at extension
 * registration. Smith doesn't expose a way to mark some OTHER conv
 * as waiting; that would need an explicit conv_id arg and the model
 * shouldn't be reaching into other conversations.
 */
import { Type } from "typebox";

import { conversationIndex } from "../conversation-index.js";

// biome-ignore lint: pi.ExtensionAPI types are still moving.
type PiExtensionAPI = any;

export function convWaitingExtension(
  pi: PiExtensionAPI,
  conversationId: string,
): void {
  pi.registerTool({
    name: "set_waiting",
    label: "Mark this conversation as waiting on an external system",
    description:
      "Use when you've done what you can do in this turn and the next " +
      "move is OUT OF YOUR HANDS — pipeline running, push notification " +
      "pending, code reviewer hasn't replied, etc. The /tasks view " +
      "moves this conv into the 等待中 column with the external label " +
      "until clear_waiting is called or the conv resumes. Stamped with " +
      "the current time on first call; re-calling just refreshes the " +
      "note. Use clear_waiting when the blocker resolves.",
    parameters: Type.Object({
      external: Type.String({
        minLength: 1,
        description:
          "Short label for what we're waiting on. Examples: " +
          "'GitLab CI', 'FortiAuthenticator', '@gaoxiaoyu', " +
          "'Mantis push'. Shown verbatim in the UI.",
      }),
      note: Type.Optional(
        Type.String({
          description:
            "Optional one-liner: what specifically we're waiting for " +
            "and how we'll know it's done. Helps future-you (or the " +
            "next person reading the task list) understand the block.",
        }),
      ),
    }),
    async execute(
      _toolCallId: string,
      params: { external: string; note?: string },
    ) {
      const entry = conversationIndex.markWaiting(
        conversationId, params.external, params.note,
      );
      if (!entry) {
        return {
          content: [{
            type: "text",
            text: `set_waiting: conv ${conversationId} not yet in index (will be after the next turn writes).`,
          }],
          details: { ok: false },
        };
      }
      return {
        content: [{
          type: "text",
          text:
            `Marked conv ${conversationId} as waiting on '${entry.waiting?.external}' ` +
            `since ${entry.waiting?.since}.`,
        }],
        details: { ok: true, external: entry.waiting?.external, since: entry.waiting?.since },
      };
    },
  });

  pi.registerTool({
    name: "clear_waiting",
    label: "Clear the waiting flag on this conversation",
    description:
      "Use when the external blocker resolved (CI passed, push got " +
      "answered, reviewer replied). The conv goes back to active/done " +
      "per the time-window heuristic. No-op if not currently waiting.",
    parameters: Type.Object({}),
    async execute(_toolCallId: string, _params: Record<string, never>) {
      const entry = conversationIndex.clearWaiting(conversationId);
      if (!entry) {
        return {
          content: [{ type: "text", text: `clear_waiting: conv ${conversationId} not tracked.` }],
          details: { ok: false },
        };
      }
      return {
        content: [{
          type: "text",
          text: entry.waiting
            ? `(unexpected) conv ${conversationId} still has waiting flag`
            : `Cleared waiting on conv ${conversationId}.`,
        }],
        details: { ok: !entry.waiting },
      };
    },
  });
}
