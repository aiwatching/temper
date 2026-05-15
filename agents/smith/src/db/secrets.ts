/**
 * Symmetric secret box. Used to encrypt plugin API keys etc. at rest
 * in the `secrets` table.
 *
 * Algorithm: AES-256-GCM. Authenticated encryption — one primitive
 * gives confidentiality + integrity, no separate HMAC step. Picked
 * over Fernet (the original spec suggestion) because GCM is in
 * node:crypto natively, no extra dep, and the ciphertext format we
 * pick is dead-simple:
 *
 *     [nonce 12B] [auth tag 16B] [ciphertext ...]   (all one BLOB)
 *
 * Master key lookup order:
 *   1. `.data/master.key` file (the canonical home — Smith owns
 *      its own data directory, the key belongs there with the DB
 *      it encrypts)
 *   2. env `SMITH_SECRET_KEY` (backward-compat for users who
 *      generated the key into .env before this change — we copy
 *      it to .data/master.key and print a notice)
 *   3. Auto-generate: write 32 random bytes to .data/master.key
 *      with mode 0600 (POSIX) and warn loudly that the operator
 *      must back it up.
 *
 * Losing the key means every stored secret becomes unrecoverable;
 * Smith keeps running but each affected plugin / setting will need
 * its secret re-entered through the UI.
 */
import {
  createCipheriv,
  createDecipheriv,
  randomBytes,
} from "node:crypto";
import {
  chmodSync,
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { dirname, resolve as resolvePath } from "node:path";

const ENV_VAR = "SMITH_SECRET_KEY";  // legacy location, still honored
const KEY_BYTES = 32;     // AES-256
const NONCE_BYTES = 12;   // GCM standard
const TAG_BYTES = 16;     // GCM standard

let _key: Buffer | null = null;

function keyPath(): string {
  return resolvePath(process.cwd(), ".data", "master.key");
}

function ensureKey(): Buffer {
  if (_key) return _key;

  // Source 1: the file (canonical home).
  const file = keyPath();
  if (existsSync(file)) {
    const raw = readFileSync(file, "utf8").trim();
    return (_key = parseKey(raw, `file ${file}`));
  }

  // Source 2: legacy .env env var. Migrate to the file so future
  // boots use the canonical path.
  const fromEnv = process.env[ENV_VAR]?.trim();
  if (fromEnv) {
    writeKeyFile(fromEnv);
    console.warn(
      `[smith.secrets] migrated ${ENV_VAR} from .env → ${file}. ` +
      `You can now remove ${ENV_VAR} from .env.`,
    );
    return (_key = parseKey(fromEnv, `migrated from ${ENV_VAR}`));
  }

  // Source 3: generate. New install.
  const generated = randomBytes(KEY_BYTES).toString("base64");
  writeKeyFile(generated);
  console.warn(
    `[smith.secrets] generated new master key at ${file}. ` +
    `BACK IT UP — it encrypts every plugin/setting secret in .data/smith.db ` +
    `and isn't recoverable from anywhere else.`,
  );
  return (_key = parseKey(generated, "newly generated"));
}

function parseKey(raw: string, source: string): Buffer {
  const buf = Buffer.from(raw, "base64");
  if (buf.length !== KEY_BYTES) {
    throw new Error(
      `master key (${source}) must decode to ${KEY_BYTES} bytes (base64); got ${buf.length}. ` +
      `Generate a fresh one: node -e "console.log(require('crypto').randomBytes(32).toString('base64'))"`,
    );
  }
  return buf;
}

function writeKeyFile(base64Key: string): void {
  const file = keyPath();
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, base64Key + "\n", { encoding: "utf8" });
  // POSIX only — chmod is a no-op on Windows volumes. Best effort:
  // a failure here doesn't block use of the key.
  try { chmodSync(file, 0o600); } catch { /* ignore */ }
}

/** Encrypt a UTF-8 string. Returns the BLOB to store in
 *  secrets.ciphertext (nonce || tag || ciphertext). */
export function encryptSecret(plaintext: string): Buffer {
  const key = ensureKey();
  const nonce = randomBytes(NONCE_BYTES);
  const cipher = createCipheriv("aes-256-gcm", key, nonce);
  const ct = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  return Buffer.concat([nonce, tag, ct]);
}

/** Decrypt a BLOB written by `encryptSecret`. Throws on auth tag
 *  mismatch (tampered or wrong key). */
export function decryptSecret(blob: Buffer): string {
  if (blob.length < NONCE_BYTES + TAG_BYTES) {
    throw new Error(`ciphertext blob too short (${blob.length} bytes)`);
  }
  const key = ensureKey();
  const nonce = blob.subarray(0, NONCE_BYTES);
  const tag = blob.subarray(NONCE_BYTES, NONCE_BYTES + TAG_BYTES);
  const ct = blob.subarray(NONCE_BYTES + TAG_BYTES);
  const decipher = createDecipheriv("aes-256-gcm", key, nonce);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(ct), decipher.final()]).toString("utf8");
}
