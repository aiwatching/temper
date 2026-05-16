/**
 * Conversation index. JSON manifest of every conv smith has seen,
 * powering the UI's "switch conversation" picker.
 *
 * Storage: `<cwd>/.data/smith-sessions/_index.json`
 * Format: { [conversationId]: IndexEntry }
 *
 * Atomic writes via tmp + rename so a smith crash mid-write doesn't
 * leave a half-baked JSON the next start can't parse.
 *
 * In-memory cache loaded lazily on first call. We re-read the file
 * before every write to merge with any out-of-band changes (e.g. a
 * second smith on a different port — unlikely but cheap to handle).
 */
import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  writeFileSync,
} from "node:fs";
import { dirname, resolve as resolvePath } from "node:path";

export interface IndexEntry {
  id: string;
  /** Short label for the picker — first user message slice, or "(untitled)". */
  title: string;
  /** Full text of the first user message, for context on hover etc. */
  firstMessage: string;
  /** ISO 8601 instant of the latest turn in this conv. */
  lastUsedAt: string;
  /** Bumped per /chat success. NOT a true message count (counts turns). */
  messageCount: number;
  /** Set when the agent / user marks this conv as blocked on an external
   *  system (CI pipeline, push notification, human reply). Aggregator
   *  promotes status to "waiting" when this is present, overriding the
   *  active/done window heuristic. Cleared when the blocker resolves. */
  waiting?: {
    external: string;   // free text label: "GitLab CI" / "FortiAuthenticator" / "@reviewer"
    since: string;      // ISO instant the wait started
    note?: string;      // optional context — what we're waiting for, expected resolution
  };
}

class ConversationIndex {
  private indexPath = resolvePath(
    process.cwd(),
    ".data",
    "smith-sessions",
    "_index.json",
  );

  private readAll(): Record<string, IndexEntry> {
    if (!existsSync(this.indexPath)) return {};
    try {
      return JSON.parse(readFileSync(this.indexPath, "utf8")) as Record<string, IndexEntry>;
    } catch (e) {
      console.warn(
        `[smith] conversation index corrupt at ${this.indexPath} — starting fresh: ${(e as Error).message}`,
      );
      return {};
    }
  }

  private writeAll(all: Record<string, IndexEntry>): void {
    mkdirSync(dirname(this.indexPath), { recursive: true });
    const tmp = this.indexPath + ".tmp";
    writeFileSync(tmp, JSON.stringify(all, null, 2));
    renameSync(tmp, this.indexPath);
  }

  list(): IndexEntry[] {
    return Object.values(this.readAll()).sort((a, b) =>
      b.lastUsedAt.localeCompare(a.lastUsedAt),
    );
  }

  get(id: string): IndexEntry | undefined {
    return this.readAll()[id];
  }

  /**
   * Upsert. On first sight of a conv we also capture the firstMessage
   * (used as the title fallback). Subsequent calls just bump
   * lastUsedAt + messageCount; the title is sticky.
   */
  recordTurn(id: string, userMessage: string): void {
    const all = this.readAll();
    const now = new Date().toISOString();
    const trimmed = userMessage.trim();
    const existing = all[id];
    if (existing) {
      all[id] = {
        ...existing,
        lastUsedAt: now,
        messageCount: existing.messageCount + 1,
      };
    } else {
      const title = trimmed
        ? trimmed.replace(/\s+/g, " ").slice(0, 60) +
          (trimmed.length > 60 ? "…" : "")
        : "(untitled)";
      all[id] = {
        id,
        title,
        firstMessage: trimmed,
        lastUsedAt: now,
        messageCount: 1,
      };
    }
    this.writeAll(all);
  }

  delete(id: string): void {
    const all = this.readAll();
    if (!(id in all)) return;
    delete all[id];
    this.writeAll(all);
  }

  /** Mark a conv as blocked on an external system. Idempotent: re-calling
   *  with the same args refreshes the note; calling with different args
   *  overwrites. Returns the updated entry or null if the conv isn't
   *  tracked yet (shouldn't happen from inside a live session, but a
   *  guard against drift). */
  markWaiting(
    id: string,
    external: string,
    note?: string,
  ): IndexEntry | null {
    const all = this.readAll();
    const e = all[id];
    if (!e) return null;
    all[id] = {
      ...e,
      waiting: {
        external: external.trim(),
        since: e.waiting?.since ?? new Date().toISOString(),
        note: note?.trim() || undefined,
      },
    };
    this.writeAll(all);
    return all[id];
  }

  /** Clear the waiting flag — call when the blocker resolves. No-op if
   *  the conv wasn't waiting in the first place. */
  clearWaiting(id: string): IndexEntry | null {
    const all = this.readAll();
    const e = all[id];
    if (!e) return null;
    if (!e.waiting) return e;
    const { waiting: _w, ...rest } = e;
    void _w;
    all[id] = rest;
    this.writeAll(all);
    return all[id];
  }
}

export const conversationIndex = new ConversationIndex();
