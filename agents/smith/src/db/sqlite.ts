/**
 * Smith's local SQLite database (`.data/smith.db`).
 *
 * better-sqlite3 is synchronous — that's fine here because Smith is a
 * single-process per-user agent; we'd rather have the simpler API +
 * tight latency than chase async wrappers. WAL mode lets the read
 * path (admin UI listing plugins, etc.) coexist with the write path
 * (PluginManager polling) without blocking each other.
 *
 * The DB holds Smith's local runtime state — plugins, secrets, and
 * later tasks/triggers. NOT user memory — that's TEMPER's job.
 *
 * Lifecycle: opened lazily on first `getDb()`. The process holds it
 * open for its lifetime; explicit close on SIGINT/SIGTERM via
 * `closeDb()` (called from index.ts).
 */
import Database from "better-sqlite3";
import { mkdirSync } from "node:fs";
import { dirname, resolve as resolvePath } from "node:path";

let _db: Database.Database | null = null;

export function getDb(): Database.Database {
  if (_db) return _db;
  const dbPath = resolvePath(process.cwd(), ".data", "smith.db");
  mkdirSync(dirname(dbPath), { recursive: true });
  _db = new Database(dbPath);
  // WAL = concurrent readers + a single writer don't block each other.
  // FK = enforce ON DELETE CASCADE etc. (SQLite has them off by default).
  _db.pragma("journal_mode = WAL");
  _db.pragma("foreign_keys = ON");
  return _db;
}

export function closeDb(): void {
  if (_db) {
    _db.close();
    _db = null;
  }
}

/**
 * Run `fn` inside a transaction. better-sqlite3 transactions are
 * synchronous — wrap a batch of operations to get atomicity without
 * dropping into raw SQL `BEGIN`/`COMMIT`.
 *
 *     tx(() => {
 *       insertA(); insertB(); updateC();
 *     });
 */
export function tx<T>(fn: () => T): T {
  return getDb().transaction(fn)();
}
