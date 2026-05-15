/**
 * SQLite CRUD for plugins + secrets.
 *
 * `upsertPlugin` takes plaintext `secret` in its input; we encrypt
 * inside this layer so the rest of the codebase never handles the
 * plaintext when round-tripping through storage (only at point-of-use
 * via `loadSecret`).
 */
import { randomBytes } from "node:crypto";

import { decryptSecret, encryptSecret } from "../db/secrets.js";
import { getDb, tx } from "../db/sqlite.js";
import type { PluginKind, PluginRow } from "./types.js";

export interface PluginUpsert {
  slug: string;
  kind: PluginKind;
  display_name: string;
  config: unknown;            // any JSON-serializable shape (kind decides)
  // Plaintext. Pass undefined to keep the existing secret. Pass null
  // to clear it. Pass a string to set/rotate.
  secret?: string | null;
  enabled?: boolean;
}

/** Random suffix prevents secret_ref collisions if you delete + recreate
 *  the same slug + the orphan secret somehow survived. */
function genSecretRef(slug: string): string {
  return `plugin/${slug}/${randomBytes(4).toString("hex")}`;
}

export function listPlugins(opts: { enabledOnly?: boolean } = {}): PluginRow[] {
  const sql = opts.enabledOnly
    ? "SELECT * FROM plugins WHERE enabled = 1 ORDER BY slug"
    : "SELECT * FROM plugins ORDER BY slug";
  return getDb().prepare(sql).all() as PluginRow[];
}

export function getPlugin(slug: string): PluginRow | null {
  const row = getDb().prepare("SELECT * FROM plugins WHERE slug = ?").get(slug);
  return (row as PluginRow) ?? null;
}

export function upsertPlugin(input: PluginUpsert): PluginRow {
  const db = getDb();
  const existing = getPlugin(input.slug);

  tx(() => {
    // Secret handling first — its ref lives on the plugin row.
    let secret_ref: string | null = existing?.secret_ref ?? null;
    if (input.secret === null && existing?.secret_ref) {
      // Explicit clear.
      db.prepare("DELETE FROM secrets WHERE ref = ?").run(existing.secret_ref);
      secret_ref = null;
    } else if (typeof input.secret === "string") {
      if (existing?.secret_ref) {
        // Rotate in place — keep the same ref so downstream caches
        // (if any) still resolve.
        db.prepare(
          "UPDATE secrets SET ciphertext = ?, updated_at = datetime('now') WHERE ref = ?",
        ).run(encryptSecret(input.secret), existing.secret_ref);
      } else {
        secret_ref = genSecretRef(input.slug);
        db.prepare("INSERT INTO secrets (ref, ciphertext) VALUES (?, ?)").run(
          secret_ref, encryptSecret(input.secret),
        );
      }
    }

    const enabled = (input.enabled ?? true) ? 1 : 0;
    if (existing) {
      db.prepare(`
        UPDATE plugins SET
          kind = ?, display_name = ?, config_json = ?,
          secret_ref = ?, enabled = ?, updated_at = datetime('now')
        WHERE slug = ?
      `).run(
        input.kind, input.display_name, JSON.stringify(input.config),
        secret_ref, enabled, input.slug,
      );
    } else {
      db.prepare(`
        INSERT INTO plugins
          (slug, kind, display_name, config_json, secret_ref, enabled)
        VALUES (?, ?, ?, ?, ?, ?)
      `).run(
        input.slug, input.kind, input.display_name,
        JSON.stringify(input.config), secret_ref, enabled,
      );
    }
  });

  // biome-ignore lint: existence guaranteed by the just-completed upsert
  return getPlugin(input.slug)!;
}

export function deletePlugin(slug: string): boolean {
  const db = getDb();
  const existing = getPlugin(slug);
  if (!existing) return false;
  tx(() => {
    if (existing.secret_ref) {
      db.prepare("DELETE FROM secrets WHERE ref = ?").run(existing.secret_ref);
    }
    db.prepare("DELETE FROM plugins WHERE slug = ?").run(slug);
  });
  return true;
}

export function setPluginHealth(
  slug: string,
  h: { ok: boolean; toolCount?: number; error?: string },
): void {
  // last_seen_at advances only on success — we want the "last time it
  // actually worked" timestamp, not "last time we probed".
  getDb().prepare(`
    UPDATE plugins SET
      last_seen_at    = CASE WHEN ? THEN datetime('now') ELSE last_seen_at END,
      last_tool_count = COALESCE(?, last_tool_count),
      last_error      = ?,
      updated_at      = datetime('now')
    WHERE slug = ?
  `).run(
    h.ok ? 1 : 0,
    h.toolCount ?? null,
    h.error ?? null,
    slug,
  );
}

/** Resolve the plaintext secret for a plugin. Returns null when the
 *  plugin has no secret_ref or the secret row is missing. */
export function loadSecret(slug: string): string | null {
  const p = getPlugin(slug);
  if (!p?.secret_ref) return null;
  const row = getDb()
    .prepare("SELECT ciphertext FROM secrets WHERE ref = ?")
    .get(p.secret_ref) as { ciphertext: Buffer } | undefined;
  if (!row) return null;
  return decryptSecret(row.ciphertext);
}
