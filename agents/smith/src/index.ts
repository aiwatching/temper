/**
 * Smith entrypoint. `npm run dev` or `node dist/index.js`.
 *
 * Boot order is load-bearing — config is now DB-first (see config.ts
 * v2 docs), so the DB has to exist before the first getConfig() call:
 *
 *   1. runMigrations()          create / upgrade .data/smith.db
 *   2. migrateEnv*()            one-time imports from .env into DB
 *                               (no-ops if already installed)
 *   3. getConfig() + mapEnvForPi()
 *                               first DB-backed read; falls back to env
 *                               for anything not yet imported
 *   4. buildApp(), serve()
 *   5. start plugin manager, consolidate scheduler, etc.
 *   6. SIGINT/SIGTERM           dispose everything in reverse
 *
 * Failure modes are LOUD and EARLY by design — a misconfig at step 3
 * crashes before we serve a single request, beats discovering it
 * mid-conversation.
 */
import { serve } from "@hono/node-server";

import { getConfig, mapEnvForPi } from "./config.js";
import { closeDb } from "./db/sqlite.js";
import { runMigrations } from "./db/migrations.js";
import { migrateEnvSettings } from "./db/migrate_env_settings.js";
import { startJobsEngine, stopJobsEngine } from "./jobs-engine.js";
import { getPluginManager } from "./plugins/manager.js";
import { migrateEnvMcpServers } from "./plugins/migrate_env.js";
import { startSchedulerIfConfigured, stopScheduler } from "./scheduler.js";
import { buildApp } from "./server.js";
import { getSessionPool } from "./session-manager.js";

function banner(): void {
  const cfg = getConfig();
  const isConfigured = Boolean(cfg.llmProvider && cfg.llmModel && cfg.temperApiKey);
  console.log(
    `\n  smith — http://${cfg.smithHost}:${cfg.smithPort}\n` +
    (isConfigured
      ? `    Temper:  ${cfg.temperBaseUrl}\n` +
        `    LLM:     ${cfg.llmProvider}/${cfg.llmModel}\n` +
        `    MCP:     ${cfg.mcpServers ? cfg.mcpServers : "(via /plugins)"}\n`
      : `    NOT YET CONFIGURED — open http://${cfg.smithHost}:${cfg.smithPort}/setup\n`) +
    `    Health:  http://${cfg.smithHost}:${cfg.smithPort}/healthz\n`,
  );
}

async function main(): Promise<void> {
  // 1. DB first — migrations create settings + plugins + secrets
  //    tables. getConfig() (which now reads from settings) won't have
  //    anything to read without this.
  runMigrations();

  // 2. One-time env → DB migrations. No-ops when destinations are
  //    already populated, so re-running is safe.
  migrateEnvSettings();      // TEMPER / LLM / SMITH_* settings → settings
  migrateEnvMcpServers();    // MCP_SERVERS → plugins

  // 3. Now safe to read config; getConfig() reads settings, falls
  //    back to env for anything not yet imported (covers the very
  //    first boot before any wizard or migration ran).
  const cfg = getConfig();
  mapEnvForPi(cfg);

  const app = buildApp();
  banner();
  serve({ fetch: app.fetch, hostname: cfg.smithHost, port: cfg.smithPort });
  startSchedulerIfConfigured();
  startJobsEngine();

  for (const sig of ["SIGINT", "SIGTERM"] as const) {
    process.on(sig, async () => {
      console.log(`\n  ${sig} — disposing sessions...`);
      stopJobsEngine();
      stopScheduler();
      await getPluginManager().disposeAll();
      await getSessionPool().disposeAll();
      closeDb();
      process.exit(0);
    });
  }
}

main().catch((err) => {
  console.error("Failed to start smith:", err);
  process.exit(1);
});
