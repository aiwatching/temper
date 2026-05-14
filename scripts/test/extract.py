"""Evaluate Graphiti extraction quality, end-to-end.

Feeds a list of sentences through `POST /v1/episodes` and prints what the
configured LLM/embedder pipeline extracts — entities (with their summary
text) and facts (RELATES_TO edges) — alongside per-sentence latency.

Use cases:
    1. Sanity-check a fresh install: are entities/facts coming back?
    2. Compare LLM providers: run with one provider, switch `.env`,
       restart the service, run again, eyeball the diff.
    3. Reproduce the "0 edges on terse sentences" pathology — useful
       when investigating extraction regressions.

Usage:
    export MS_BASE_URL=http://localhost:18088
    export MS_API_KEY=mk_yourkeyhere

    # default corpus — 8 sentences covering name/place/relation cases
    python3 scripts/test/extract.py

    # custom corpus from stdin (one sentence per line)
    cat my-sentences.txt | python3 scripts/test/extract.py --stdin

    # delete the episodes after — useful when running repeatedly
    python3 scripts/test/extract.py --cleanup

The script writes into whatever namespace your API key resolves to (your
own `user:<id>` by default). Pass --namespace to override.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import httpx

DEFAULT_CORPUS = [
    # Short, terse — historically the LLM extracted 0 edges from these.
    "Hi.",
    "Who is my English teacher?",
    # One-clue sentences — should yield 1 entity, ideally 1 fact.
    "My English teacher is Yalena.",
    "Sarah lives in Toronto.",
    # Multi-clue — should produce multiple facts.
    "My teacher Yalena lives in Vancouver and teaches me on Saturdays.",
    "Jerry switched English teachers; the new one is Sarah from Toronto.",
    # Time-aware — exercises Graphiti's temporal reasoning.
    "Last week Yalena was my Spanish teacher, but now she teaches me English.",
    # Long, prose-style — different surface form, should still extract.
    (
        "Jerry started learning English in March. He uses three apps: "
        "Duolingo for vocabulary, Anki for spaced repetition, and "
        "Speechling for pronunciation feedback."
    ),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default=os.environ.get("MS_BASE_URL", "http://localhost:18088"))
    ap.add_argument("--key", default=os.environ.get("MS_API_KEY"))
    ap.add_argument("--namespace", default=None, help="Override target namespace")
    ap.add_argument("--stdin", action="store_true", help="Read sentences from stdin, one per line")
    ap.add_argument("--cleanup", action="store_true", help="DELETE episodes after the run")
    ap.add_argument("--json", action="store_true", help="Machine-readable output")
    args = ap.parse_args()

    if not args.key:
        ap.error("MS_API_KEY env var or --key required")

    sentences = _read_corpus(args)
    if not sentences:
        ap.error("no sentences to process")

    client = httpx.Client(
        base_url=args.base_url.rstrip("/"),
        headers={"X-API-Key": args.key, "Content-Type": "application/json"},
        timeout=120.0,
    )

    results: list[dict[str, Any]] = []
    written: list[str] = []
    for i, text in enumerate(sentences, 1):
        body: dict[str, Any] = {"content": text}
        if args.namespace:
            body["namespace"] = args.namespace
        t0 = time.perf_counter()
        r = client.post("/v1/episodes", json=body)
        elapsed = time.perf_counter() - t0
        if r.status_code >= 400:
            print(f"[{i}] {text!r}  →  HTTP {r.status_code}: {r.text}", file=sys.stderr)
            continue
        d = r.json()
        written.append(d["episode_id"])
        rec = {
            "i": i,
            "text": text,
            "elapsed_s": round(elapsed, 2),
            "entities": [
                {"name": e["name"], "summary": e.get("summary")}
                for e in d["extracted_entities"]
            ],
            "facts": [f["fact"] for f in d["extracted_facts"]],
        }
        results.append(rec)
        if not args.json:
            _print_one(rec)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        _print_summary(results)

    if args.cleanup:
        if not args.json:
            print(f"\n  cleaning up {len(written)} episode(s)...")
        for eid in written:
            client.delete(f"/v1/episodes/{eid}")

    return 0


def _read_corpus(args: argparse.Namespace) -> list[str]:
    if args.stdin:
        return [ln.strip() for ln in sys.stdin if ln.strip()]
    return DEFAULT_CORPUS


def _print_one(rec: dict[str, Any]) -> None:
    bar = "─" * 70
    print(f"\n{bar}")
    print(f"[{rec['i']:>2}]  ({rec['elapsed_s']}s)  {rec['text']}")
    if rec["entities"]:
        print(f"     entities ({len(rec['entities'])}):")
        for e in rec["entities"]:
            sm = e["summary"] or ""
            if len(sm) > 90:
                sm = sm[:87] + "..."
            print(f"       - {e['name']}: {sm}")
    else:
        print("     entities: (none)")
    if rec["facts"]:
        print(f"     facts ({len(rec['facts'])}):")
        for f in rec["facts"]:
            print(f"       • {f}")
    else:
        print("     facts: (none)")


def _print_summary(results: list[dict[str, Any]]) -> None:
    n = len(results)
    if not n:
        return
    zero_entities = sum(1 for r in results if not r["entities"])
    zero_facts = sum(1 for r in results if not r["facts"])
    total_entities = sum(len(r["entities"]) for r in results)
    total_facts = sum(len(r["facts"]) for r in results)
    total_time = sum(r["elapsed_s"] for r in results)
    bar = "═" * 70
    print(f"\n{bar}")
    print(f"  sentences:        {n}")
    print(f"  total entities:   {total_entities}  (avg {total_entities/n:.1f}/sentence)")
    print(f"  total facts:      {total_facts}  (avg {total_facts/n:.1f}/sentence)")
    print(f"  zero-entity:      {zero_entities}/{n}  ({100*zero_entities/n:.0f}%)")
    print(f"  zero-fact:        {zero_facts}/{n}  ({100*zero_facts/n:.0f}%)")
    print(f"  total latency:    {total_time:.1f}s  (avg {total_time/n:.1f}s/sentence)")


if __name__ == "__main__":
    sys.exit(main())
