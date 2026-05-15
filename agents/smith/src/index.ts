/**
 * Smith entrypoint. `npm run dev` or `node dist/index.js`.
 *
 * Boots in this order so a missing dep fails LOUD and EARLY rather than
 * mid-conversation:
 *   1. Load + freeze config from .env
 *   2. Map LLM_* env into the names pi-ai expects (PI_TELEMETRY=0 too)
 *   3. Build Hono app, start listening
 *   4. SIGINT/SIGTERM → dispose every active session, exit
 */
import { serve } from "@hono/node-server";

import { getConfig, mapEnvForPi } from "./config.js";
import { startSchedulerIfConfigured, stopScheduler } from "./scheduler.js";
import { buildApp } from "./server.js";
import { getSessionPool } from "./session-manager.js";

function banner(): void {
  const cfg = getConfig();
  console.log(
    `\n  smith — http://${cfg.smithHost}:${cfg.smithPort}\n` +
    `    Temper:  ${cfg.temperBaseUrl}\n` +
    `    LLM:     ${cfg.llmProvider}/${cfg.llmModel}\n` +
    `    MCP:     ${cfg.mcpServers ? cfg.mcpServers : "(none configured)"}\n` +
    `    Health:  http://${cfg.smithHost}:${cfg.smithPort}/healthz\n`,
  );
}

async function main(): Promise<void> {
  const cfg = getConfig();
  mapEnvForPi(cfg);

  const app = buildApp();
  banner();
  serve({ fetch: app.fetch, hostname: cfg.smithHost, port: cfg.smithPort });
  startSchedulerIfConfigured();

  for (const sig of ["SIGINT", "SIGTERM"] as const) {
    process.on(sig, async () => {
      console.log(`\n  ${sig} — disposing sessions...`);
      stopScheduler();
      await getSessionPool().disposeAll();
      process.exit(0);
    });
  }
}

main().catch((err) => {
  console.error("Failed to start smith:", err);
  process.exit(1);
});
