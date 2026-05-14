"""Graphiti capability showcase — 5 scenarios you can't do with vector RAG.

This isn't a benchmark (see scripts/test/extract.py for that). It's a
narrated walkthrough that picks one ability per scenario and runs a tiny
synthetic example so you can see the behavior end-to-end. After it
finishes, open the admin graph viewer to see the resulting knowledge
graph visually:

  http://localhost:18088/admin/graph        # our viewer
  http://localhost:3000/                   # FalkorDB's own Browser

Scenarios:
  1. Temporal contradiction handling    — facts get invalidated when newer
                                          info supersedes them.
  2. Entity deduplication               — repeated mentions collapse into
                                          one node with an aggregated summary.
  3. Multi-hop walk                     — graph structure lets you walk
                                          A → B → C, not just retrieve A.
  4. Hybrid retrieval                   — search by meaning ("teacher") hits
                                          text that says "instructor."
  5. Source attribution                 — every fact links back to the
                                          original episodes it came from.
  6. Time-travel queries (`as_of`)      — ask the same question against
                                          different points in time, get
                                          different answers.

Usage:
    export MS_BASE_URL=http://localhost:18088
    export MS_API_KEY=mk_yourkeyhere

    python3 examples/graphiti_showcase.py             # run all
    python3 examples/graphiti_showcase.py --cleanup   # delete demo data after

Tagged 'graphiti-showcase' so cleanup is selective — your real episodes
in this namespace are untouched.

The demo writes to your own `user:<id>` namespace. Pass --namespace to
override (e.g. dedicated demo group).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta

import httpx

TAG = "graphiti-showcase"


# ---- HTTP plumbing ------------------------------------------------------


def client(args: argparse.Namespace) -> httpx.Client:
    if not args.key:
        sys.exit("MS_API_KEY env var or --key required")
    return httpx.Client(
        base_url=args.base_url.rstrip("/"),
        headers={"X-API-Key": args.key, "Content-Type": "application/json"},
        timeout=120.0,
    )


def write(c: httpx.Client, args, content: str, *, reference_time: datetime | None = None) -> dict:
    body = {"content": content, "tags": [TAG]}
    if args.namespace:
        body["namespace"] = args.namespace
    if reference_time:
        body["reference_time"] = reference_time.isoformat()
    t0 = time.perf_counter()
    r = c.post("/v1/episodes", json=body)
    r.raise_for_status()
    d = r.json()
    print(f"    ↪ wrote {d['episode_id'][:8]}…  ({time.perf_counter()-t0:.1f}s)  "
          f"{len(d['extracted_entities'])} entities, {len(d['extracted_facts'])} facts")
    return d


def search(
    c: httpx.Client,
    query: str,
    *,
    limit: int = 5,
    as_of: datetime | None = None,
) -> list[dict]:
    params: dict[str, str | int] = {"query": query, "limit": limit}
    if as_of is not None:
        params["as_of"] = as_of.isoformat()
    r = c.get("/v1/search", params=params)
    r.raise_for_status()
    return r.json()["facts"]


def list_demo_episodes(c: httpx.Client) -> list[dict]:
    """Anything we wrote in this run is tagged TAG — for cleanup. /v1/episodes
    caps `limit` at 100 so we page until empty."""
    out: list[dict] = []
    cursor: str | None = None
    while True:
        params: dict[str, str | int] = {"limit": 100}
        if cursor:
            params["before"] = cursor
        r = c.get("/v1/episodes", params=params)
        r.raise_for_status()
        body = r.json()
        out.extend(e for e in body["episodes"] if TAG in (e.get("tags") or []))
        cursor = body.get("next_cursor")
        if not cursor:
            break
    return out


def cleanup(c: httpx.Client) -> None:
    rows = list_demo_episodes(c)
    print(f"\n  cleanup: removing {len(rows)} demo episode(s)…")
    for e in rows:
        c.delete(f"/v1/episodes/{e['episode_id']}")
    print("  done.")


# ---- pretty printing ----------------------------------------------------


def section(title: str, desc: str) -> None:
    bar = "═" * 78
    print(f"\n{bar}\n  {title}\n  {desc}\n{bar}")


def step(label: str) -> None:
    print(f"\n  • {label}")


def show_facts(facts: list[dict], header: str = "facts") -> None:
    if not facts:
        print(f"    [{header}] (none)")
        return
    print(f"    [{header}] {len(facts)}:")
    for f in facts:
        valid = "active" if not f.get("invalid_at") else f"invalid_at={f['invalid_at'][:10]}"
        marker = "✓" if not f.get("invalid_at") else "✗"
        print(f"      {marker} [{f['kind']}, {valid}] {f['fact']}")


# ---- scenarios ----------------------------------------------------------


def scenario_temporal(c: httpx.Client, args) -> None:
    section(
        "1. Temporal contradiction handling",
        "Old facts auto-invalidate when newer info contradicts them.",
    )
    yesterday = datetime.now(UTC) - timedelta(days=1)
    step(f"Write at T0 (yesterday): Jerry's English teacher is Sarah.")
    write(c, args, "Jerry's English teacher is Sarah.", reference_time=yesterday)
    step(f"Write at T1 (now): Jerry switched teachers — new teacher is Mike.")
    write(c, args, "Jerry switched English teachers. His new teacher is Mike.")
    step("Search 'Jerry English teacher' — Sarah's fact should be marked invalid_at, Mike's should be active.")
    show_facts(search(c, "Jerry's English teacher", limit=10))
    print("\n    → Note: Graphiti doesn't delete the old fact — it marks it invalid_at,")
    print("      letting you do time-travel queries (e.g. 'who was teaching as of T0?').")


def scenario_deduplication(c: httpx.Client, args) -> None:
    section(
        "2. Entity deduplication across episodes",
        "Three episodes mention Sarah differently — graph keeps ONE Sarah node.",
    )
    for s in [
        "Sarah lives in Toronto.",
        "Sarah works as an English teacher.",
        "I'm meeting Sarah on Saturday morning.",
    ]:
        step(f"Write: {s}")
        write(c, args, s)
    step("List facts about Sarah — should show several edges anchored to one Entity:Sarah.")
    show_facts(search(c, "Sarah", limit=10))
    print("\n    → Open /admin/graph and you'll see ONE orange node 'Sarah' with three")
    print("      blue Episodic nodes feeding it MENTIONS edges. No duplicates.")


def scenario_multi_hop(c: httpx.Client, args) -> None:
    section(
        "3. Multi-hop walk",
        "Vector RAG retrieves matches; a graph lets you traverse: Sarah → Jerry → city.",
    )
    step("Write: Sarah teaches Jerry English.")
    write(c, args, "Sarah teaches Jerry English.")
    step("Write: Jerry lives in Mountain View.")
    write(c, args, "Jerry lives in Mountain View.")
    step("Search 'where does Sarah's student live' — direct match unlikely; the graph still encodes the path.")
    show_facts(search(c, "where does Sarah's student live", limit=10))
    print("\n    → In /admin/graph, you should see a path:")
    print("        Sarah ──teaches──> Jerry ──lives_in──> Mountain View")
    print("      That's the multi-hop view vector RAG can't give you. memctl graph")
    print("      cypher 'MATCH (a {name:\"Sarah\"})-[*1..3]-(b) RETURN a,b' walks it.")


def scenario_hybrid_search(c: httpx.Client, args) -> None:
    section(
        "4. Hybrid retrieval — meaning, not just words",
        "Search 'teacher' hits text that says 'instructor', via embedding similarity.",
    )
    step("Write: Sarah is the instructor of my Saturday morning English course.")
    write(c, args, "Sarah is the instructor of my Saturday morning English course.")
    step("Search 'teacher' — semantic match on 'instructor'.")
    show_facts(search(c, "teacher", limit=5))
    print("\n    → Graphiti runs BM25 + cosine similarity + (optional) cross-encoder")
    print("      reranking in parallel and fuses with RRF. Even when the exact word")
    print("      is absent, semantically-close facts still surface.")


def scenario_time_travel(c: httpx.Client, args) -> None:
    section(
        "6. Time-travel queries (`as_of`)",
        "Same question, three points in time — three different answers.",
    )
    now = datetime.now(UTC)
    t_old = now - timedelta(days=10)
    t_mid = now - timedelta(days=3)

    step(f"Write at T_old (10 days ago):  Karen's project lead was Alice.")
    write(c, args, "Karen's project lead is Alice.", reference_time=t_old)
    step(f"Write at T_mid (3 days ago):   Karen got reassigned — new lead is Bob.")
    write(c, args, "Karen's project lead is now Bob.", reference_time=t_mid)
    step(f"Write at T_new (now):          Reorg — Karen's lead is Carol.")
    write(c, args, "Karen's project lead is now Carol.")

    def _show_facts_only(label: str, facts: list[dict], *, time_travel: bool) -> None:
        # The as_of filter applies to RELATES_TO edges. Entity summaries
        # are always the merged view (they don't have valid_at/invalid_at
        # the same way), so we filter them out to keep the demo clear.
        facts = [f for f in facts if f["kind"] == "fact" and "karen" in f["fact"].lower()]
        print(f"    [{label}]")
        if not facts:
            print("      (no facts about Karen at this point)")
            return
        for f in facts:
            if time_travel:
                # Everything in this list was active at as_of by definition.
                valid_at = (f.get("valid_at") or "")[:10]
                print(f"      ✓ [active @ as_of, valid_at={valid_at}] {f['fact']}")
            else:
                valid = "active" if not f.get("invalid_at") else f"invalid_at={f['invalid_at'][:10]}"
                marker = "✓" if not f.get("invalid_at") else "✗"
                print(f"      {marker} [{valid}] {f['fact']}")

    step("Search WITHOUT as_of — what the system believes right now:")
    _show_facts_only(
        "current", search(c, "Karen's project lead", limit=20), time_travel=False
    )

    for label, when in [
        ("as_of = 5 days ago (between T_mid and now)", now - timedelta(days=5)),
        ("as_of = 7 days ago (between T_old and T_mid)", now - timedelta(days=7)),
        ("as_of = 15 days ago (before any write)",       now - timedelta(days=15)),
    ]:
        step(f"Search WITH {label}:")
        _show_facts_only(
            label,
            search(c, "Karen's project lead", limit=20, as_of=when),
            time_travel=True,
        )

    print("\n    → Filter: valid_at <= as_of  AND  (invalid_at IS NULL OR invalid_at > as_of)")
    print("      Time-travel applies to RELATES_TO facts. Entity-node summaries are always")
    print("      the merged view — you saw them in earlier scenarios as kind=\"entity\".")
    print("      Use cases: compliance ('show what we knew on date X'), replay,")
    print("      historical regression analysis.")


def scenario_attribution(c: httpx.Client, args) -> None:
    section(
        "5. Source attribution",
        "Every fact is linked back to the episode(s) that produced it.",
    )
    facts = search(c, "Sarah", limit=3)
    if not facts:
        print("    [no facts to demonstrate — re-run with prior scenarios]")
        return
    fact = next((f for f in facts if f["kind"] == "fact" and f["source_episode_ids"]), facts[0])
    step(f"Pick a fact: {fact['fact']}")
    ep_ids = fact.get("source_episode_ids") or []
    if not ep_ids:
        print("    (this hit was an entity summary — no source_episode_ids; pick a 'fact' kind)")
        return
    step(f"Trace to original episode {ep_ids[0][:8]}…")
    r = c.get(f"/v1/episodes/{ep_ids[0]}")
    r.raise_for_status()
    ep = r.json()
    print(f"      → original: '{ep.get('content')}'")
    print(f"      → written:  {ep.get('created_at')}  by  {ep.get('created_by_agent')}")
    print("\n    → Use this to answer 'why does the LLM think X?' — point back to the")
    print("      exact source sentence and timestamp.")


# ---- main ---------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=os.environ.get("MS_BASE_URL", "http://localhost:18088"))
    ap.add_argument("--key", default=os.environ.get("MS_API_KEY"))
    ap.add_argument("--namespace", default=None, help="Override target namespace")
    ap.add_argument("--cleanup", action="store_true", help="Delete demo episodes after the run")
    ap.add_argument(
        "--only",
        type=int,
        choices=[1, 2, 3, 4, 5, 6],
        help="Run only scenario N (useful for re-trying a single demo)",
    )
    args = ap.parse_args()

    c = client(args)
    scenarios = [
        scenario_temporal,
        scenario_deduplication,
        scenario_multi_hop,
        scenario_hybrid_search,
        scenario_attribution,
        scenario_time_travel,
    ]
    runs = [scenarios[args.only - 1]] if args.only else scenarios
    for fn in runs:
        try:
            fn(c, args)
        except httpx.HTTPError as exc:
            print(f"\n  [scenario failed: {exc}]", file=sys.stderr)
            print(f"  body: {getattr(exc, 'response', None) and exc.response.text}", file=sys.stderr)

    print("\n" + "═" * 78)
    print("  Done. Now look at the graph visually:")
    print("    • Our viewer:        http://localhost:18088/admin/graph")
    print("    • FalkorDB browser:  http://localhost:3000/")
    print("═" * 78)

    if args.cleanup:
        cleanup(c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
