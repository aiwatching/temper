/**
 * Settings storage — flat KV in `.data/smith.db`, with sensitive
 * values transparently encrypted via the existing `secrets` table.
 *
 * Plaintext values are JSON-encoded (so we can round-trip booleans,
 * numbers, strings, simple objects without coercion). Sensitive
 * values write a row into `secrets` and stash the ref on the
 * `settings.secret_ref` column.
 *
 * What's a "setting" key looks like:
 *
 *   installed                    'true' | 'false' (bootstrap marker)
 *   temper.base_url              'http://127.0.0.1:18088'
 *   temper.api_key               <secret>
 *   llm.provider                 'deepseek'
 *   llm.base_url                 'http://...'
 *   llm.model                    'forti-k2'
 *   llm.api_key                  <secret>
 *   smith.agent_slug             'smith'
 *   smith.bearer_secret          <secret>   optional; empty => no auth
 *   consolidate.schedule_hours   number
 *   consolidate.auto_apply       boolean
 *   recall.log_level             'quiet' | 'verbose' | 'full' | 'dump'
 *   plugins.poll_seconds         number
 *
 * The list above is convention, not enforced. Any key works.
 */
import { randomBytes } from "node:crypto";

import { decryptSecret, encryptSecret } from "./secrets.js";
import { getDb, tx } from "./sqlite.js";

interface SettingRow {
  key: string;
  value: string | null;
  secret_ref: string | null;
  description: string | null;
  updated_at: string;
  updated_by: string | null;
}

/** Plaintext settings expose this shape to UI / callers. */
export interface SettingOut {
  key: string;
  // For non-secret entries: the parsed JSON value (any shape).
  // For secret entries: undefined; check `has_secret`.
  value?: unknown;
  has_secret: boolean;
  description: string | null;
  updated_at: string;
  updated_by: string | null;
}

function rowToOut(row: SettingRow): SettingOut {
  return {
    key: row.key,
    value: row.value !== null ? safeParse(row.value) : undefined,
    has_secret: row.secret_ref !== null,
    description: row.description,
    updated_at: row.updated_at,
    updated_by: row.updated_by,
  };
}

function safeParse(s: string): unknown {
  try { return JSON.parse(s); } catch { return s; }
}

function genSecretRef(key: string): string {
  return `setting/${key}/${randomBytes(4).toString("hex")}`;
}

/** Read a plaintext setting. Returns null when unset. */
export function getSetting(key: string): unknown {
  const row = getDb()
    .prepare("SELECT value FROM settings WHERE key = ? AND value IS NOT NULL")
    .get(key) as { value: string } | undefined;
  return row ? safeParse(row.value) : null;
}

/** Read a secret setting (decrypts on demand). Returns null when unset. */
export function getSecretSetting(key: string): string | null {
  const row = getDb()
    .prepare("SELECT secret_ref FROM settings WHERE key = ?")
    .get(key) as { secret_ref: string | null } | undefined;
  if (!row?.secret_ref) return null;
  const sec = getDb()
    .prepare("SELECT ciphertext FROM secrets WHERE ref = ?")
    .get(row.secret_ref) as { ciphertext: Buffer } | undefined;
  if (!sec) return null;
  return decryptSecret(sec.ciphertext);
}

/** Convenience accessor with a default. */
export function getSettingOr<T>(key: string, fallback: T): T {
  const v = getSetting(key);
  return (v ?? fallback) as T;
}

/** Write a plaintext setting. Any JSON-encodable value. */
export function setSetting(
  key: string, value: unknown,
  opts: { description?: string; updatedBy?: string } = {},
): void {
  const db = getDb();
  const encoded = JSON.stringify(value);
  const existing = db
    .prepare("SELECT secret_ref FROM settings WHERE key = ?")
    .get(key) as { secret_ref: string | null } | undefined;
  tx(() => {
    // If the key was previously a secret, drop the old secret row —
    // overwriting with plaintext means it isn't sensitive anymore.
    if (existing?.secret_ref) {
      db.prepare("DELETE FROM secrets WHERE ref = ?").run(existing.secret_ref);
    }
    if (existing) {
      db.prepare(`
        UPDATE settings SET
          value = ?, secret_ref = NULL,
          description = COALESCE(?, description),
          updated_at = datetime('now'), updated_by = ?
        WHERE key = ?
      `).run(encoded, opts.description ?? null, opts.updatedBy ?? null, key);
    } else {
      db.prepare(`
        INSERT INTO settings (key, value, secret_ref, description, updated_by)
        VALUES (?, ?, NULL, ?, ?)
      `).run(key, encoded, opts.description ?? null, opts.updatedBy ?? null);
    }
  });
}

/** Write a secret setting (encrypts, stores in `secrets`, refs from
 *  `settings`). Pass `null` to clear both. */
export function setSecretSetting(
  key: string, secret: string | null,
  opts: { description?: string; updatedBy?: string } = {},
): void {
  const db = getDb();
  const existing = db
    .prepare("SELECT secret_ref FROM settings WHERE key = ?")
    .get(key) as { secret_ref: string | null } | undefined;

  tx(() => {
    let secret_ref: string | null = existing?.secret_ref ?? null;

    if (secret === null) {
      // Clear.
      if (existing?.secret_ref) {
        db.prepare("DELETE FROM secrets WHERE ref = ?").run(existing.secret_ref);
      }
      secret_ref = null;
    } else if (existing?.secret_ref) {
      // Rotate in place, keep ref.
      db.prepare(
        "UPDATE secrets SET ciphertext = ?, updated_at = datetime('now') WHERE ref = ?",
      ).run(encryptSecret(secret), existing.secret_ref);
    } else {
      secret_ref = genSecretRef(key);
      db.prepare("INSERT INTO secrets (ref, ciphertext) VALUES (?, ?)").run(
        secret_ref, encryptSecret(secret),
      );
    }

    if (existing) {
      db.prepare(`
        UPDATE settings SET
          value = NULL, secret_ref = ?,
          description = COALESCE(?, description),
          updated_at = datetime('now'), updated_by = ?
        WHERE key = ?
      `).run(secret_ref, opts.description ?? null, opts.updatedBy ?? null, key);
    } else {
      db.prepare(`
        INSERT INTO settings (key, value, secret_ref, description, updated_by)
        VALUES (?, NULL, ?, ?, ?)
      `).run(key, secret_ref, opts.description ?? null, opts.updatedBy ?? null);
    }
  });
}

/** Delete a setting (and its secret if any). */
export function deleteSetting(key: string): boolean {
  const db = getDb();
  const existing = db
    .prepare("SELECT secret_ref FROM settings WHERE key = ?")
    .get(key) as { secret_ref: string | null } | undefined;
  if (!existing) return false;
  tx(() => {
    if (existing.secret_ref) {
      db.prepare("DELETE FROM secrets WHERE ref = ?").run(existing.secret_ref);
    }
    db.prepare("DELETE FROM settings WHERE key = ?").run(key);
  });
  return true;
}

/** Dump all settings (without decrypting secrets). Used by /settings
 *  UI; pair with explicit getSecretSetting calls for individual secret
 *  reads when actually needed (never bulk-dumped to the UI). */
export function listSettings(prefix?: string): SettingOut[] {
  const db = getDb();
  const sql = prefix
    ? "SELECT * FROM settings WHERE key LIKE ? ORDER BY key"
    : "SELECT * FROM settings ORDER BY key";
  const rows = (prefix
    ? db.prepare(sql).all(`${prefix}%`)
    : db.prepare(sql).all()) as SettingRow[];
  return rows.map(rowToOut);
}

/** True iff `installed` setting is the string 'true'. The bootstrap
 *  uses this to decide whether to gate routes through /setup. */
export function isInstalled(): boolean {
  return getSetting("installed") === true;
}

/** Mark first-run setup complete. Idempotent. */
export function markInstalled(updatedBy = "setup-wizard"): void {
  setSetting("installed", true, {
    description: "First-run setup marker. Routes redirect to /setup when this isn't true.",
    updatedBy,
  });
}
