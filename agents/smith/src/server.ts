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

    // Collect streamed assistant text; pi delivers it via subscribe()
    // events of type "text_delta". We buffer the whole turn for the MVP
    // and return one JSON; SSE upgrade lives in a follow-up.
    let reply = "";
    // biome-ignore lint: pi's event types are still moving — typed locally.
    const unsubscribe = session.subscribe((e: any) => {
      if (e?.type === "message_update" && e.assistantMessageEvent?.type === "text_delta") {
        reply += String(e.assistantMessageEvent.delta ?? "");
      }
    });
    try {
      await session.prompt(message);
    } finally {
      unsubscribe();
    }
    return c.json({ conversationId, reply });
  });

  return app;
}
