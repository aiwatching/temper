/**
 * pi extension: exposes TEMPER's memory write/search as two tools to the
 * LLM tool loop. The shape mirrors what we taught agents in TEMPER's
 * /admin/integrate page — same field names, same semantics — so the model
 * doesn't need to learn anything new.
 *
 * Registered via `DefaultResourceLoader.extensionFactories` from index.ts.
 */
import { Type } from "typebox";

import { Temper } from "../temper.js";

/**
 * `pi` here is the ExtensionAPI handle pi-coding-agent passes to factories.
 * We type it loosely (`any`) for now because pi's SDK types are still in
 * flux and we don't want to chase upstream renames every release. The
 * surface we touch is documented in pi's `extensions/types.ts`:
 *   - pi.registerTool({ name, label, description, parameters, execute })
 *
 * Keep the surface narrow so when the API stabilises we tighten the type
 * in one place.
 */
// biome-ignore lint: pi.ExtensionAPI's types are still moving — see comment.
type PiExtensionAPI = any;

export function temperMemoryExtension(pi: PiExtensionAPI): void {
  const temper = new Temper();

  // ---- memory_search ----
  pi.registerTool({
    name: "memory_search",
    label: "Search memory",
    description:
      "Semantic search over the user's long-term memory in TEMPER. Call " +
      "this at task start, when the user references prior context " +
      "(\"as I mentioned\", \"last time\"), or before answering anything " +
      "personal. Hits include fact, valid_at, invalid_at, score — surface " +
      "only the top 1–3 paraphrased.",
    parameters: Type.Object({
      query: Type.String({
        description: "Free-text search terms — keywords or a short question.",
      }),
      limit: Type.Optional(
        Type.Integer({
          minimum: 1,
          maximum: 25,
          default: 5,
          description: "Max hits to return. Default 5.",
        }),
      ),
      as_of: Type.Optional(
        Type.String({
          format: "date-time",
          description:
            "ISO-8601 instant. Returns facts that were active at that time " +
            "(handles 'what was true last week?' questions).",
        }),
      ),
      namespaces: Type.Optional(
        Type.Array(Type.String(), {
          description:
            "Restrict scope. Omit to use your default agent namespace + " +
            "cross-agent recall (recommended).",
        }),
      ),
    }),
    async execute(
      _toolCallId: string,
      params: { query: string; limit?: number; as_of?: string; namespaces?: string[] },
    ) {
      const hits = await temper.search({
        query: params.query,
        limit: params.limit,
        asOf: params.as_of,
        namespaces: params.namespaces,
      });
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({ hits }, null, 2),
          },
        ],
        details: { hitCount: hits.length },
      };
    },
  });

  // ---- memory_write ----
  pi.registerTool({
    name: "memory_write",
    label: "Write memory",
    description:
      "Write one fact to TEMPER. Use sparingly — one discrete fact per " +
      "call, never dump whole transcripts. Call when the user shares a " +
      "preference, decision, identity fact, or anything durable that may " +
      "be recalled later. Never store credentials or unconsented PII. " +
      "Contradictions: just write the new state; Temper handles temporal " +
      "invalidation.",
    parameters: Type.Object({
      content: Type.String({
        minLength: 1,
        description:
          "One or two sentences capturing what to remember, paraphrased " +
          "rather than verbatim transcript.",
      }),
      source_description: Type.Optional(
        Type.String({
          default: "user said this in smith chat",
          description: "Where this fact came from (chat / email / doc).",
        }),
      ),
      reference_time: Type.Optional(
        Type.String({
          format: "date-time",
          description:
            "When the event actually happened. Defaults to now if omitted.",
        }),
      ),
      tags: Type.Optional(Type.Array(Type.String())),
      saga: Type.Optional(
        Type.String({
          description:
            "Optional saga name to chain related episodes (e.g. a single " +
            "conversation). Episodes sharing a saga get linked via " +
            "NEXT_EPISODE edges.",
        }),
      ),
      namespace: Type.Optional(
        Type.String({
          description:
            "Override default namespace. Use 'user:me' for cross-agent " +
            "recall, otherwise leave blank to keep memory in this agent's " +
            "private scope.",
        }),
      ),
    }),
    async execute(
      _toolCallId: string,
      params: {
        content: string;
        source_description?: string;
        reference_time?: string;
        tags?: string[];
        saga?: string;
        namespace?: string;
      },
    ) {
      const result = await temper.write({
        content: params.content,
        sourceType: "message",
        sourceDescription: params.source_description ?? "user said this in smith chat",
        referenceTime: params.reference_time,
        tags: params.tags,
        saga: params.saga,
        namespace: params.namespace,
      });
      return {
        content: [
          {
            type: "text",
            text: JSON.stringify(result),
          },
        ],
        details: {},
      };
    },
  });
}
