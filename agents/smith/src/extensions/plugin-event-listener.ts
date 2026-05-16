/**
 * pi extension: bridges pi's `tool_execution_end` event into the
 * job scheduler so jobs registered with trigger_kind="plugin_event"
 * fire when a matching tool completes.
 *
 * Why this exists vs reacting to events directly in jobs-engine.ts:
 *   - pi events are PER SESSION. The job engine lives in process scope.
 *   - Subscribing in an extension means every session attaches one
 *     listener, and we get to see the per-session conversationId via
 *     closure (which we need for loop prevention — see below).
 *
 * What this DOESN'T do:
 *   - MCP server push notifications. Vanilla MCP doesn't surface
 *     server-initiated events to the host; "plugin_event" here means
 *     "Smith called a plugin tool and we want to react to it", not
 *     "the plugin pushed something to us". If you need the latter you
 *     need an MCP server that opens a websocket or similar — out of
 *     scope for this commit.
 *
 * Loop prevention: if a job fires a synthetic conversation that calls
 * a plugin tool, that tool's tool_execution_end could match this same
 * job again → infinite loop. Synthetic conv ids start with "job-", so
 * we just bail when the originating session is one. Users can still
 * chain jobs by having job A target a tool that fires job B (different
 * convIds), but a job can't recursively fire itself.
 *
 * Matching rules (trigger_config schema for plugin_event):
 *   {
 *     plugin_slug: "mantis" | "*",       // tool name prefix; * = any
 *     event: "tool_end" | "tool_end:<tool_name>",  // stage + opt name
 *     on_error?: boolean                 // true = only fire when isError
 *   }
 *
 * Smith's plugin convention is `<slug>__<tool>` — the bridge in
 * plugins/mcp.ts builds tool names that way. We split on the first
 * "__" to derive (slug, tool) from a tool_execution_end event.
 */
import { listJobs } from "../db/jobs-repo.js";
import { runJobNow } from "../jobs-engine.js";

// biome-ignore lint: pi.ExtensionAPI types are still moving.
type PiExtensionAPI = any;

interface ToolExecutionEndEvent {
  type: "tool_execution_end";
  toolCallId: string;
  toolName: string;
  result: unknown;
  isError: boolean;
}

interface PluginEventConfig {
  plugin_slug: string;
  event: string;
  on_error?: boolean;
}

function parseToolName(toolName: string): { slug: string; tool: string } {
  const idx = toolName.indexOf("__");
  if (idx === -1) {
    // Built-in / smith-owned tool with no plugin namespace. Use
    // "smith" as the conventional slug so jobs can target the
    // agent's own tool surface explicitly if they want.
    return { slug: "smith", tool: toolName };
  }
  return { slug: toolName.slice(0, idx), tool: toolName.slice(idx + 2) };
}

function jobMatches(
  cfg: PluginEventConfig,
  parsed: { slug: string; tool: string },
  isError: boolean,
): boolean {
  if (cfg.on_error && !isError) return false;
  if (cfg.plugin_slug !== "*" && cfg.plugin_slug !== parsed.slug) return false;

  // Event: "tool_end" matches all tools; "tool_end:<tool>" matches the
  // specific tool name. Empty / undefined defaults to "tool_end".
  const e = (cfg.event ?? "tool_end").trim();
  if (e === "tool_end" || e === "*") return true;
  if (e.startsWith("tool_end:")) {
    return e.slice("tool_end:".length) === parsed.tool;
  }
  // Unknown event spec: don't match. Future-proofs against new
  // categories (e.g. "tool_start:") without firing on them today.
  return false;
}

export function pluginEventListenerExtension(
  pi: PiExtensionAPI,
  conversationId: string,
): void {
  // The originating session for the event is the one whose pi
  // instance fires the handler. We capture it via closure.
  const isJobConv = conversationId.startsWith("job-");

  pi.on("tool_execution_end", (event: ToolExecutionEndEvent) => {
    // Loop guard: a job's own tool calls don't re-fire matching jobs.
    if (isJobConv) return;

    const parsed = parseToolName(event.toolName);
    const candidates = listJobs({ enabled: true, triggerKind: "plugin_event" });
    if (candidates.length === 0) return;

    for (const job of candidates) {
      const cfg = job.trigger_config as PluginEventConfig;
      if (!jobMatches(cfg, parsed, event.isError)) continue;

      // Fire and forget. Failures inside the job get recorded by the
      // engine (recordRun) and written to audit.log — we just kick it
      // off and return.
      console.log(
        `[smith.jobs] plugin_event match: ${event.toolName} ` +
        `(isError=${event.isError}) → firing job ${job.id} (${job.name})`,
      );
      runJobNow(job).catch((e) => {
        console.warn(
          `[smith.jobs] plugin_event fire of ${job.id} failed: ${(e as Error).message}`,
        );
      });
    }
  });
}
