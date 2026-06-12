#!/usr/bin/env python3
"""One-time memory hygiene pass for a TEMPER namespace.

Targets the four pollution patterns found in production (2026-06):

  1. junk episodes      — empty content, "no substantive information"
                          summarizer output, sub-20-char fragments.
                          Deleted via DELETE /v1/episodes/{id} (cleans
                          postgres + the graph node together).
  2. fact:* blocks      — one-off facts mirrored into the KV store;
                          the same content already lives in episodes.
                          Deleted via DELETE /v1/memory/blocks/{key}.
  3. chat:*:summary:*   — per-segment chat summaries written as blocks.
     blocks               Migrated to ONE document per chat
                          (chats/<chat_id>.md, segments in time order),
                          then the blocks are deleted.
  4. junk graph entities — bare MR numbers, BRANCH=/MODEL= env strings,
                          URLs, file paths, sentence-length names.
                          The cypher API is read-only, so the script
                          WRITES A SHELL SCRIPT of redis-cli commands
                          to run on the server (docker compose exec).

Default is DRY-RUN: scan + report + write the graph script, mutate
nothing. Re-run with --apply --i-have-a-backup to execute.

SAFETY (learned the hard way — a prior version deleted 1138 episodes):
deletion targets are chosen by POSITIVE confirmation only. An episode
is deleted only if we read its content and it matched a junk pattern,
or /status authoritatively reported extraction_status == "failed".
Anything unreadable, pending, or that errored during the scan is NEVER
deleted. Orphan episodes (zero MENTIONS) are report-only — they should
be re-extracted, not deleted. --apply refuses to run without
--i-have-a-backup (run ./deploy.sh backup on the server first).

Usage:
  ./scripts/cleanup_memory.py --base http://host:18088 --token mk_...                       # dry-run
  ./scripts/cleanup_memory.py --base ... --token ... --apply --i-have-a-backup              # execute
  ./scripts/cleanup_memory.py --base ... --token ... --apply --i-have-a-backup --failed     # + failed rows
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from typing import Any

# ---------------------------------------------------------------- http


def make_client(base: str, token: str):
    base = base.rstrip("/")

    def request(method: str, path: str, body: dict | None = None) -> Any:
        headers: dict[str, str] = {"Accept": "application/json"}
        if token.startswith("mk_"):
            headers["X-API-Key"] = token
        else:
            headers["Authorization"] = f"Bearer {token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        # The target server may be under live agent write load — 5xx
        # and timeouts are transient there. Retry with backoff before
        # giving up; 4xx are real answers and surface immediately.
        last_exc: Exception | None = None
        for attempt in range(3):
            req = urllib.request.Request(
                base + path, data=data, method=method, headers=headers,
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    raw = r.read()
                    return json.loads(raw) if raw.strip() else None
            except urllib.error.HTTPError as e:
                if e.code < 500:
                    raise
                last_exc = e
            except (TimeoutError, OSError) as e:
                last_exc = e
            import time
            time.sleep(2 * (attempt + 1))
        raise last_exc  # type: ignore[misc]

    return request


# ---------------------------------------------------------------- junk rules

# Episode content that should never have been written. Conservative:
# every pattern here was observed in the production scan.
EPISODE_JUNK_PATTERNS = [
    re.compile(r"no substantive information", re.I),
    re.compile(r"^\s*→\s*ok", re.I),
    re.compile(r"truncated or (incomplete|malformed)", re.I),
]
EPISODE_MIN_CHARS = 20

# Entity names that are transient identifiers or extraction accidents,
# not knowledge. Matched against the FULL name.
ENTITY_JUNK_PATTERNS = [
    re.compile(r"^!?\d{4,6}$"),                  # bare MR / bug numbers
    re.compile(r"^(BRANCH|MODEL|TAG|ENV)="),     # env-var assignments
    re.compile(r"^https?://"),                   # URLs
    re.compile(r"^/"),                           # absolute file paths
    re.compile(r"^[Bb]uild #\d+$"),              # individual build runs
    re.compile(r"^[Pp]ipeline [0-9a-f]{6,}$"),   # pipeline run ids
    re.compile(r"^chat:"),                       # chat-id leakage
    re.compile(r"^TCM Case ID \d+$"),            # individual test case ids
    re.compile(r".{80,}"),                       # sentence-length "names"
]


def episode_is_junk(content: str | None) -> str | None:
    if content is None:
        return None  # unreadable (failed/pending) — separate bucket
    stripped = content.strip()
    if not stripped:
        return "empty content"
    if len(stripped) < EPISODE_MIN_CHARS:
        return f"only {len(stripped)} chars"
    for pat in EPISODE_JUNK_PATTERNS:
        if pat.search(stripped):
            return f"matches junk pattern {pat.pattern!r}"
    return None


def entity_is_junk(name: str) -> bool:
    return any(p.search(name) for p in ENTITY_JUNK_PATTERNS)


# ---------------------------------------------------------------- graph name

def graphiti_group_id(raw_ns: str) -> str:
    """Mirror Namespace.as_graphiti_group_id (core/namespaces.py)."""
    return raw_ns.replace(":", "__").replace("/", "_").replace("-", "_")


# ---------------------------------------------------------------- steps


def scan_episodes(req, namespace: str, throttle_ms: int = 0) -> dict:
    """Classify every episode by POSITIVE signals only.

    SAFETY INVARIANT (the rule a prior version violated and deleted
    1138 episodes): an episode is only ever put in a *deletable* bucket
    when we have positively confirmed it belongs there —

      junk     : we read its content and it matched a junk pattern.
      failed   : /status SQL authoritatively reported extraction_status
                 == "failed".

    Anything we could not read, or that is "pending", or whose status
    request errored, goes to a NON-deletable bucket. We NEVER infer
    "delete this" from absence (e.g. "no graph node found") — async
    writes legitimately have metadata.id != graph uuid, so absence
    proves nothing.

    Two requests per episode: /status (cheap, SQL-only, authoritative)
    then /{id} detail only for `done` episodes (the FalkorDB read for
    content). `throttle_ms` inserts a pause between episodes to spare
    FalkorDB's single worker when the live agent is writing.
    """
    import time

    print("→ 扫描 episodes(逐条 /status,done 的再读内容)...", file=sys.stderr)
    ns_q = urllib.parse.quote(namespace)
    episodes: list[dict] = []
    cursor = None
    while True:
        path = f"/v1/episodes?namespace={ns_q}&limit=100"
        if cursor:
            path += f"&before={urllib.parse.quote(cursor)}"
        d = req("GET", path)
        batch = d.get("episodes", [])
        episodes.extend(batch)
        cursor = d.get("next_cursor")
        if not cursor or not batch:
            break

    junk: list[dict] = []          # deletable: positively-read junk content
    failed: list[str] = []         # deletable: status == "failed"
    pending: list[str] = []        # NOT deletable: still extracting
    scan_errors: list[str] = []    # NOT deletable: request failed
    ok = 0
    for i, e in enumerate(episodes):
        if i and i % 200 == 0:
            print(f"   ... {i}/{len(episodes)}", file=sys.stderr)
        eid = e["episode_id"]
        try:
            st = req("GET", f"/v1/episodes/{eid}/status")
        except Exception:
            scan_errors.append(eid)
            continue
        status = st.get("extraction_status")
        if status == "failed":
            failed.append(eid)
            continue
        if status == "pending":
            pending.append(eid)
            continue
        # status == "done" — read content to test for junk.
        try:
            detail = req("GET", f"/v1/episodes/{eid}")
        except Exception:
            scan_errors.append(eid)
            continue
        reason = episode_is_junk(detail.get("content"))
        if reason:
            junk.append({
                "id": eid,
                "reason": reason,
                "preview": (detail.get("content") or "").strip()[:80],
            })
        else:
            ok += 1
        if throttle_ms:
            time.sleep(throttle_ms / 1000)

    return {"total": len(episodes), "ok": ok, "junk": junk,
            "failed": failed, "pending": pending, "scan_errors": scan_errors}


def scan_orphans(req, namespace: str) -> list[str]:
    """Report-only: count graph episodic nodes with no MENTIONS edge.

    Returns graph uuids — NOT used for deletion (they aren't metadata
    ids, and orphans should be re-extracted, not deleted). Purely
    informational so the report can show coverage."""
    try:
        d = req("POST", "/v1/graph/cypher", {
            "namespace": namespace,
            "query": "MATCH (ep:Episodic) WHERE NOT (ep)-[:MENTIONS]->() RETURN count(ep) AS c",
        })
        rows = d.get("rows", [])
        return rows[0]["c"] if rows else 0
    except Exception:
        return -1  # couldn't measure


def scan_blocks(req) -> dict:
    d = req("GET", "/v1/memory/blocks?scope=both")
    blocks = d.get("blocks", [])
    fact = [b for b in blocks if b["block_key"].startswith("fact:")]
    chat = [b for b in blocks if b["block_key"].startswith("chat:")]
    other = [b for b in blocks if b not in fact and b not in chat]
    return {"total": len(blocks), "fact": fact, "chat": chat, "other_count": len(other)}


def scan_entities(req, namespace: str) -> dict:
    d = req("POST", "/v1/graph/cypher", {
        "namespace": namespace,
        # OPTIONAL MATCH instead of a count{} subquery — FalkorDB
        # doesn't support the newer subquery syntax.
        "query": (
            "MATCH (e:Entity) OPTIONAL MATCH (e)-[r]-() "
            "RETURN e.name AS name, count(r) AS deg"
        ),
    })
    rows = d.get("rows", [])
    junk = [r for r in rows if r.get("name") and entity_is_junk(r["name"])]
    # exact dup names (after lowercase) — report only; merging needs
    # property-preserving edge moves better done by a server-side
    # consolidation pass.
    by_norm: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        if r.get("name"):
            by_norm[r["name"].lower().strip()].append(r["name"])
    dups = {k: v for k, v in by_norm.items() if len(v) > 1}
    return {"total": len(rows), "junk": junk, "dup_groups": dups}


def chat_blocks_to_documents(req, chat_blocks: list[dict], apply: bool) -> list[str]:
    """Group chat:<id>:summary:<ts> blocks into one markdown doc per
    chat, newest segment last. Returns the doc paths written."""
    by_chat: dict[str, list[dict]] = defaultdict(list)
    key_re = re.compile(r"^chat:([0-9a-f-]+):summary:(\d+)$")
    for b in chat_blocks:
        m = key_re.match(b["block_key"])
        if not m:
            continue
        by_chat[m.group(1)].append({"ts": int(m.group(2)), "block": b})

    paths = []
    failed: list[str] = []
    for chat_id, segs in sorted(by_chat.items()):
        segs.sort(key=lambda s: s["ts"])
        parts = [f"# Chat {chat_id} — session summaries\n"]
        for s in segs:
            val = s["block"]["block_value"]
            text = val.get("text") if isinstance(val, dict) else str(val)
            parts.append(f"\n## segment @{s['ts']}\n\n{text}\n")
        path = f"chats/{chat_id}.md"
        if apply:
            # Per-chat failures must not abort the whole run — retry
            # once (transient 5xx happens when the live agent is
            # writing concurrently), then record and move on. Blocks
            # belonging to failed chats are NOT deleted by the caller.
            ok = False
            for _attempt in range(2):
                try:
                    req("PUT", f"/v1/documents/{path}", {
                        "title": f"Chat {chat_id} summaries",
                        "content": "".join(parts),
                        "source": "cleanup-migration",
                        "tags": ["chat-summary", "migrated"],
                    })
                    ok = True
                    break
                except Exception as exc:
                    last_err = exc
            if not ok:
                print(f"  ! chat {chat_id}: {last_err}")
                failed.append(chat_id)
                continue
        paths.append(path)
    if failed:
        print(f"  ! {len(failed)} chat(s) failed to migrate — their blocks are kept")
    return paths, failed


def write_graph_script(namespace: str, junk_entities: list[dict], out_path: str) -> None:
    """Emit redis-cli commands (run ON THE SERVER) that DETACH DELETE
    the junk entity nodes. The HTTP cypher API is read-only by design,
    so graph surgery goes through the falkordb container directly."""
    graph = graphiti_group_id(namespace)
    lines = [
        "#!/usr/bin/env bash",
        "# Generated by cleanup_memory.py — junk graph-entity removal.",
        "# RUN ON THE TEMPER SERVER, from anywhere inside the temper",
        "# checkout (docker compose v2 finds the compose file by walking",
        "# up parent directories). Review the names below before running;",
        "# each DETACH DELETE also removes the node's MENTIONS /",
        "# RELATES_TO edges.",
        "set -euo pipefail",
        "",
    ]
    # Batch 20 names per query to keep command lines manageable.
    names = [e["name"] for e in junk_entities]
    for i in range(0, len(names), 20):
        batch = names[i:i + 20]
        quoted = ", ".join("'" + n.replace("\\", "\\\\").replace("'", "\\'") + "'" for n in batch)
        cypher = f"MATCH (e:Entity) WHERE e.name IN [{quoted}] DETACH DELETE e"
        lines.append(
            "docker compose exec -T falkordb redis-cli GRAPH.QUERY "
            f"'{graph}' \"{cypher}\""
        )
    lines.append("echo done — deleted up to %d junk entities." % len(names))
    import os
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------- main


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", required=True, help="TEMPER base URL")
    ap.add_argument("--token", required=True, help="API key (mk_...) or JWT of the namespace owner")
    ap.add_argument("--namespace", default=None,
                    help="Target namespace. Default: the token's own agent namespace (resolved via /v1/namespaces).")
    ap.add_argument("--apply", action="store_true", help="Execute deletions/migration (default: dry-run)")
    ap.add_argument("--failed", action="store_true",
                    help="ALSO delete episodes whose extraction_status is "
                         "authoritatively 'failed' (no graph data was ever "
                         "produced). Positively confirmed via /status; never "
                         "inferred. Off by default.")
    ap.add_argument("--throttle-ms", type=int, default=0,
                    help="Pause between per-episode reads to spare FalkorDB's "
                         "single worker when the live agent is writing. e.g. 50")
    ap.add_argument("--i-have-a-backup", action="store_true",
                    help="Required with --apply: confirms you ran "
                         "'./deploy.sh backup' on the server first. There is no "
                         "undo for deletions.")
    ap.add_argument("--graph-script", default=".data/cleanup-graph.sh",
                    help="Output path for the server-side junk-entity removal script "
                         "(default under .data/ — runtime artifacts dir, gitignored)")
    args = ap.parse_args()

    if args.apply and not args.i_have_a_backup:
        sys.exit(
            "REFUSING to --apply without --i-have-a-backup.\n"
            "Deletions are irreversible and this service has no automatic\n"
            "snapshots. On the server run:  ./deploy.sh backup\n"
            "then re-run with --apply --i-have-a-backup."
        )

    req = make_client(args.base, args.token)

    ns = args.namespace
    if not ns:
        spaces = req("GET", "/v1/namespaces")["namespaces"]
        agent_ns = [s["raw"] for s in spaces if s["kind"] == "agent"]
        if len(agent_ns) != 1:
            sys.exit(f"--namespace required; token can read: {[s['raw'] for s in spaces]}")
        ns = agent_ns[0]
    print(f"namespace: {ns}")
    print(f"mode:      {'APPLY' if args.apply else 'DRY-RUN'}\n")

    # ---- scan ----
    ep = scan_episodes(req, ns, throttle_ms=args.throttle_ms)
    junk_ids = {j["id"] for j in ep["junk"]}
    orphan_count = scan_orphans(req, ns)
    bl = scan_blocks(req)
    en = scan_entities(req, ns)

    # ---- report ----
    print("== EPISODES ==")
    print(f"  total {ep['total']} | clean {ep['ok']} | junk {len(ep['junk'])} (deletable)")
    for j in ep["junk"][:10]:
        print(f"    [{j['reason']}] {j['preview']!r}")
    if len(ep["junk"]) > 10:
        print(f"    ... and {len(ep['junk']) - 10} more")
    print(f"  failed (status=failed, no graph data): {len(ep['failed'])}"
          + ("  → WILL DELETE (--failed)" if args.failed else "  → kept (pass --failed to delete)"))
    print(f"  pending (still extracting): {len(ep['pending'])}  → NEVER deleted")
    if ep["scan_errors"]:
        print(f"  scan errors (read failed — NEVER deleted, re-run to retry): {len(ep['scan_errors'])}")
    if orphan_count >= 0:
        print(f"  orphans (in graph, zero MENTIONS): {orphan_count}  → report only "
              "(re-extract candidates, never auto-deleted)")

    print("\n== BLOCKS ==")
    print(f"  total {bl['total']} | fact:* {len(bl['fact'])} (delete) | chat:*:summary {len(bl['chat'])} (migrate→documents) | other {bl['other_count']} (kept)")

    print("\n== GRAPH ENTITIES ==")
    print(f"  total {en['total']} | junk {len(en['junk'])} (server-side script) | exact-dup groups {len(en['dup_groups'])} (report only)")
    for e in en["junk"][:10]:
        print(f"    deg={e['deg']}  {e['name'][:70]}")
    if en["dup_groups"]:
        print("  dup samples:", list(en["dup_groups"].values())[:5])

    write_graph_script(ns, en["junk"], args.graph_script)
    print(f"\n  graph 手术脚本已生成: {args.graph_script} (拷到服务器 temper 目录下执行)")

    if not args.apply:
        print("\nDRY-RUN 结束,没有改任何数据。确认无误后加 --apply 执行。")
        return

    # ---- apply ----
    print("\n== APPLYING ==")
    deleted_eps = 0
    # ONLY positively-classified deletables. junk = read-confirmed junk
    # content; failed = /status-confirmed extraction failure. Never
    # orphans, never pending, never scan-errors.
    targets = list(junk_ids) + (ep["failed"] if args.failed else [])
    for i, eid in enumerate(targets):
        try:
            req("DELETE", f"/v1/episodes/{eid}")
            deleted_eps += 1
        except Exception as exc:
            print(f"  ! episode {eid}: {exc}")
        if i and i % 100 == 0:
            print(f"  ... episodes {i}/{len(targets)}")
    print(f"  episodes deleted: {deleted_eps}/{len(targets)}")

    paths, failed_chats = chat_blocks_to_documents(req, bl["chat"], apply=True)
    print(f"  chat summaries migrated into {len(paths)} documents")

    # Don't delete summary blocks for chats whose document failed to
    # write — that would destroy the only copy.
    deletable_chat = [
        b for b in bl["chat"]
        if not any(b["block_key"].startswith(f"chat:{cid}:") for cid in failed_chats)
    ]
    deleted_blocks = 0
    for b in bl["fact"] + deletable_chat:
        try:
            req("DELETE", f"/v1/memory/blocks/{urllib.parse.quote(b['block_key'], safe='')}")
            deleted_blocks += 1
        except Exception as exc:
            print(f"  ! block {b['block_key']}: {exc}")
    print(f"  blocks deleted: {deleted_blocks}/{len(bl['fact']) + len(deletable_chat)}")

    print("\n完成。下一步: 把 graph 手术脚本拷到服务器执行,然后跑")
    print("  curl -X POST .../v1/admin/communities/build   # 重建社区聚类")


if __name__ == "__main__":
    main()
