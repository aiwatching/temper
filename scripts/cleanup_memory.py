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
nothing. Re-run with --apply to execute steps 1-3.

Orphan episodes (graph node exists but has zero MENTIONS edges) are
reported but only deleted with --orphans: their content is real, they
are just invisible to graph search. Decide after reading the report.

Usage:
  ./scripts/cleanup_memory.py --base http://host:18088 --token mk_...           # dry-run
  ./scripts/cleanup_memory.py --base ... --token ... --apply                    # execute
  ./scripts/cleanup_memory.py --base ... --token ... --apply --orphans          # + orphans
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
        req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return json.loads(raw) if raw.strip() else None

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


def scan_episodes(req, namespace: str) -> dict:
    print("→ 扫描 episodes(逐条拉内容,1000+ 条需要几分钟)...", file=sys.stderr)
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

    junk: list[dict] = []          # {id, reason, preview}
    unreadable: list[str] = []     # failed/pending — content is None
    ok = 0
    for i, e in enumerate(episodes):
        if i and i % 200 == 0:
            print(f"   ... {i}/{len(episodes)}", file=sys.stderr)
        try:
            detail = req("GET", f"/v1/episodes/{e['episode_id']}")
        except Exception as exc:
            unreadable.append(e["episode_id"])
            continue
        content = detail.get("content")
        reason = episode_is_junk(content)
        if content is None:
            unreadable.append(e["episode_id"])
        elif reason:
            junk.append({
                "id": e["episode_id"],
                "reason": reason,
                "preview": (content or "").strip()[:80],
            })
        else:
            ok += 1
    return {"total": len(episodes), "ok": ok, "junk": junk, "unreadable": unreadable}


def scan_orphans(req, namespace: str) -> list[str]:
    """Graph episodes with no MENTIONS edge. Returns graphiti uuids
    (== metadata ids for sync writes)."""
    d = req("POST", "/v1/graph/cypher", {
        "namespace": namespace,
        "query": "MATCH (ep:Episodic) WHERE NOT (ep)-[:MENTIONS]->() RETURN ep.uuid AS u",
    })
    return [r["u"] for r in d.get("rows", [])]


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
    for chat_id, segs in sorted(by_chat.items()):
        segs.sort(key=lambda s: s["ts"])
        parts = [f"# Chat {chat_id} — session summaries\n"]
        for s in segs:
            val = s["block"]["block_value"]
            text = val.get("text") if isinstance(val, dict) else str(val)
            parts.append(f"\n## segment @{s['ts']}\n\n{text}\n")
        path = f"chats/{chat_id}.md"
        paths.append(path)
        if apply:
            req("PUT", f"/v1/documents/{path}", {
                "title": f"Chat {chat_id} summaries",
                "content": "".join(parts),
                "source": "cleanup-migration",
                "tags": ["chat-summary", "migrated"],
            })
    return paths


def write_graph_script(namespace: str, junk_entities: list[dict], out_path: str) -> None:
    """Emit redis-cli commands (run ON THE SERVER) that DETACH DELETE
    the junk entity nodes. The HTTP cypher API is read-only by design,
    so graph surgery goes through the falkordb container directly."""
    graph = graphiti_group_id(namespace)
    lines = [
        "#!/usr/bin/env bash",
        "# Generated by cleanup_memory.py — junk graph-entity removal.",
        "# RUN ON THE TEMPER SERVER (needs the falkordb container).",
        "# Review the names below before running. Each DETACH DELETE also",
        "# removes the node's MENTIONS / RELATES_TO edges.",
        "set -euo pipefail",
        "cd \"$(dirname \"$0\")\"",
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
    ap.add_argument("--orphans", action="store_true",
                    help="ALSO delete orphan episodes (graph node with no MENTIONS). Off by default.")
    ap.add_argument("--failed", action="store_true",
                    help="ALSO delete unreadable episodes (extraction failed/pending — "
                         "postgres metadata only, content was never stored). Off by default.")
    ap.add_argument("--graph-script", default="cleanup-graph.sh",
                    help="Output path for the server-side junk-entity removal script")
    args = ap.parse_args()

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
    ep = scan_episodes(req, ns)
    orphans = scan_orphans(req, ns)
    junk_ids = {j["id"] for j in ep["junk"]}
    orphan_only = [u for u in orphans if u not in junk_ids]
    bl = scan_blocks(req)
    en = scan_entities(req, ns)

    # ---- report ----
    print("== EPISODES ==")
    print(f"  total {ep['total']} | clean {ep['ok']} | junk {len(ep['junk'])} | unreadable(failed/pending) {len(ep['unreadable'])}")
    for j in ep["junk"][:10]:
        print(f"    [{j['reason']}] {j['preview']!r}")
    if len(ep["junk"]) > 10:
        print(f"    ... and {len(ep['junk']) - 10} more")
    print(f"  orphans (in graph, zero MENTIONS, excl. junk): {len(orphan_only)}"
          + ("  → WILL DELETE (--orphans)" if args.orphans else "  → kept (pass --orphans to delete)"))
    print(f"  unreadable (failed/pending, content never stored): {len(ep['unreadable'])}"
          + ("  → WILL DELETE (--failed)" if args.failed else "  → kept (pass --failed to delete)"))

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
    targets = list(junk_ids) + (orphan_only if args.orphans else []) \
        + (ep["unreadable"] if args.failed else [])
    for i, eid in enumerate(targets):
        try:
            req("DELETE", f"/v1/episodes/{eid}")
            deleted_eps += 1
        except Exception as exc:
            print(f"  ! episode {eid}: {exc}")
        if i and i % 100 == 0:
            print(f"  ... episodes {i}/{len(targets)}")
    print(f"  episodes deleted: {deleted_eps}/{len(targets)}")

    paths = chat_blocks_to_documents(req, bl["chat"], apply=True)
    print(f"  chat summaries migrated into {len(paths)} documents")

    deleted_blocks = 0
    for b in bl["fact"] + bl["chat"]:
        try:
            req("DELETE", f"/v1/memory/blocks/{urllib.parse.quote(b['block_key'], safe='')}")
            deleted_blocks += 1
        except Exception as exc:
            print(f"  ! block {b['block_key']}: {exc}")
    print(f"  blocks deleted: {deleted_blocks}/{len(bl['fact']) + len(bl['chat'])}")

    print("\n完成。下一步: 把 graph 手术脚本拷到服务器执行,然后跑")
    print("  curl -X POST .../v1/admin/communities/build   # 重建社区聚类")


if __name__ == "__main__":
    main()
