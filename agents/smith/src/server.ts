/**
 * Smith's HTTP control plane. Tiny — anything that wants to drive Smith
 * (a CLI, a web UI, an IDE plugin, an iOS shortcut) speaks plain JSON
 * over HTTP at the same port.
 *
 *   GET  /healthz                       liveness — Temper reachable + LLM creds
 *   POST /chat   body:{message,conversationId?}   single turn
 *
 * Future: streaming SSE on /chat once we wire `session.subscribe()`
 * through to the response body. MVP returns a single JSON when the turn
 * completes.
 */
import { Hono } from "hono";

import { getConfig } from "./config.js";
import { Temper, TemperError } from "./temper.js";
import { getSessionPool } from "./session-manager.js";

export function buildApp(): Hono {
  const app = new Hono();

  // ---- health ----
  app.get("/healthz", async (c) => {
    const cfg = getConfig();
    const body: Record<string, unknown> = {
      status: "ok",
      temper_base_url: cfg.temperBaseUrl,
      llm_provider: cfg.llmProvider,
      llm_model: cfg.llmModel,
      active_sessions: getSessionPool().count(),
    };
    try {
      const t = new Temper();
      await t.health();
      const me = await t.whoami();
      body.temper_user = me.email;
    } catch (e) {
      body.status = "degraded";
      body.temper_error = e instanceof TemperError ? e.detail : String(e);
    }
    return c.json(body);
  });

  // ---- chat ----
  app.post("/chat", async (c) => {
    type ChatBody = { message?: string; conversationId?: string };
    let body: ChatBody;
    try {
      body = (await c.req.json()) as ChatBody;
    } catch {
      return c.json({ error: "Body must be JSON" }, 400);
    }
    const message = (body.message ?? "").trim();
    if (!message) return c.json({ error: "message is required" }, 400);

    const conversationId = body.conversationId?.trim() || "default";
    const pool = getSessionPool();
    const session = await pool.getOrCreate(conversationId);

    // Collect the assistant's final reply via `agent_end` — fires when
    // the whole turn (including any tool roundtrips) is done and carries
    // the complete message transcript. `text_delta` events also stream
    // for short replies, but the model often emits no delta when a turn
    // is dominated by tool calls; reading from the final transcript
    // is robust to both cases.
    //
    // SSE upgrade will switch to streaming `text_delta` directly through
    // the response body; agent_end stays as the close signal.
    // Pull reply + any provider-level error out of the final agent_end.
    // pi's `prompt()` resolves cleanly even when Anthropic / OpenAI / etc
    // returned a 4xx — the failure shows up as `stopReason: "error"` on
    // the last assistant message with the detail in `errorMessage`.
    // Surface it explicitly so the caller doesn't get a silent empty reply.
    let reply = "";
    let stopReason: string | undefined;
    let errorMessage: string | undefined;
    // biome-ignore lint: pi's event union is wide — typed locally.
    const unsubscribe = session.subscribe((e: any) => {
      if (e?.type !== "agent_end") return;
      const msgs: any[] = e.messages ?? [];
      for (let i = msgs.length - 1; i >= 0; i--) {
        const m = msgs[i];
        if (m?.role !== "assistant") continue;
        stopReason = m.stopReason;
        errorMessage = m.errorMessage;
        for (const block of m.content ?? []) {
          if (block?.type === "text" && typeof block.text === "string") {
            reply += block.text;
          }
        }
        break;
      }
    });
    try {
      await session.prompt(message);
    } finally {
      unsubscribe();
    }
    if (stopReason === "error") {
      return c.json(
        { conversationId, error: errorMessage ?? "LLM error (no detail)", stopReason },
        502,
      );
    }
    return c.json({ conversationId, reply, stopReason });
  });

  return app;
}
