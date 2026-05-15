/**
 * Forward-only migration runner for Smith's SQLite DB.
 *
 * Convention: numbered SQL files in `src/db/migrations/<NNN>_<name>.sql`,
 * applied in order. Already-applied migrations are tracked in a
 * `schema_migrations` table. We don't support down migrations — this
 * is a per-user single-process DB; if something needs reverting,
 * write a new forward migration that undoes it.
 *
 * Migrations are SQL only (no programmatic ones). For data fixups
 * that need code, do them post-migration in the caller.
 *
 * On boot, `runMigrations()` is called once from index.ts before
 * anything else touches the DB.
 */
import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve as resolvePath } from "node:path";
import { fileURLToPath } from "node:url";

import { getDb } from "./sqlite.js";

const _here = dirname(fileURLToPath(import.meta.url));

/** Locate the migrations directory. Works in both `tsx watch src/...`
 *  (file lives under `src/db/`) and `node dist/...` (under `dist/db/`,
 *  but the .sql files were copied there during build OR we walk up to
 *  the source tree). We try both. */
function findMigrationsDir(): string | null {
  const candidates = [
    resolvePath(_here, "migrations"),                       // dev: tsx watch
    resolvePath(_here, "..", "..", "src", "db", "migrations"),  // from dist
  ];
  for (const p of candidates) {
    try {
      if (readdirSync(p).some((f) => f.endsWith(".sql"))) return p;
    } catch {
      /* keep looking */
    }
  }
  return null;
}

export function runMigrations(): { applied: string[]; skipped: string[] } {
  const db = getDb();
  db.exec(`
    CREATE TABLE IF NOT EXISTS schema_migrations (
      version    TEXT PRIMARY KEY,
      filename   TEXT NOT NULL,
      applied_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
  `);

  const dir = findMigrationsDir();
  if (!dir) {
    console.warn("[db] no migrations directory found — skipping");
    return { applied: [], skipped: [] };
  }

  const files = readdirSync(dir)
    .filter((f) => /^\d{3}_.*\.sql$/.test(f))
    .sort();

  const appliedSet = new Set(
    (db.prepare("SELECT version FROM schema_migrations").all() as { version: string }[])
      .map((r) => r.version),
  );

  const applied: string[] = [];
  const skipped: string[] = [];
  for (const file of files) {
    const version = file.slice(0, 3);
    if (appliedSet.has(version)) {
      skipped.push(file);
      continue;
    }
    const sql = readFileSync(resolvePath(dir, file), "utf8");
    console.log(`[db] applying migration ${file}`);
    db.transaction(() => {
      db.exec(sql);
      db.prepare("INSERT INTO schema_migrations (version, filename) VALUES (?, ?)")
        .run(version, file);
    })();
    applied.push(file);
  }
  if (applied.length > 0) {
    console.log(`[db] migrations: applied ${applied.length}, already-applied ${skipped.length}`);
  }
  return { applied, skipped };
}
