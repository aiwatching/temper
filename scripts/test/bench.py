#!/usr/bin/env python3
"""Honest capacity benchmark — runs several profiles in one go.

Hits each endpoint with N concurrent in-flight requests and reports
throughput + p50 / p95 / p99 latency. Useful for answering "how many
users can this thing actually handle?" without doing math in your head.

Profiles (all share a single configured user / API key):

  auth-me      cheap auth round-trip (baseline for asyncio overhead)
  search       semantic search — hits FalkorDB + embedder
  episodes-ls  list episodes — Postgres only
  health       no auth — pure FastAPI dispatch
  write-sync   POST /v1/episodes — LLM-bound, worst case
  write-async  POST /v1/episodes?async_extract=true — returns immediately

Usage:
    export MS_BASE_URL=http://localhost:18088
    export MS_API_KEY=mk_...
    python3 scripts/test/bench.py                  # default profile mix
    python3 scripts/test/bench.py --only write-async --num 100 --concurrency 30
    python3 scripts/test/bench.py --skip write-sync   # skip the slowest
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time
from datetime import UTC, datetime

import httpx


# ----------------------------------------------------------------------


class Profile:
    name: str
    method: str
    path: str
    body_fn = None
    auth: bool = True

    def __init__(self, name, method, path, *, body_fn=None, auth=True):
        self.name, self.method, self.path = name, method, path
        self.body_fn, self.auth = body_fn, auth


def write_body() -> dict:
    return {
        "content": f"Bench note {datetime.now(UTC).isoformat()}",
        "tags": ["bench"],
    }


PROFILES: list[Profile] = [
    Profile("health",      "GET",  "/v1/health", auth=False),
    Profile("auth-me",     "GET",  "/v1/auth/me"),
    Profile("episodes-ls", "GET",  "/v1/episodes?limit=20"),
    Profile("search",      "GET",  "/v1/search?query=teacher&limit=10"),
    Profile("write-async", "POST", "/v1/episodes?async_extract=true", body_fn=write_body),
    Profile("write-sync",  "POST", "/v1/episodes", body_fn=write_body),
]


# ----------------------------------------------------------------------


async def one_call(client, profile, headers):
    t0 = time.perf_counter()
    try:
        if profile.method == "GET":
            r = await client.get(profile.path, headers=headers, timeout=60.0)
        else:
            body = profile.body_fn() if profile.body_fn else {}
            r = await client.post(profile.path, headers=headers, json=body, timeout=60.0)
        return r.status_code, (time.perf_counter() - t0) * 1000.0
    except Exception:
        return 0, (time.perf_counter() - t0) * 1000.0


async def run_profile(client, profile, *, num, concurrency, headers):
    sem = asyncio.Semaphore(concurrency)

    async def worker():
        async with sem:
            return await one_call(client, profile, headers)

    start = time.perf_counter()
    results = await asyncio.gather(*(worker() for _ in range(num)))
    elapsed = time.perf_counter() - start

    by_code: dict[int, int] = {}
    lats: list[float] = []
    for code, ms in results:
        by_code[code] = by_code.get(code, 0) + 1
        if code != 0:
            lats.append(ms)
    lats.sort()
    return {
        "elapsed_s": elapsed,
        "throughput": num / elapsed if elapsed > 0 else 0,
        "by_code": dict(sorted(by_code.items())),
        "min":  lats[0] if lats else 0,
        "p50":  statistics.median(lats) if lats else 0,
        "p95":  lats[int(len(lats) * 0.95) - 1] if len(lats) >= 20 else lats[-1] if lats else 0,
        "p99":  lats[int(len(lats) * 0.99) - 1] if len(lats) >= 100 else lats[-1] if lats else 0,
        "max":  lats[-1] if lats else 0,
        "n":    num,
        "conc": concurrency,
    }


def print_row(profile_name, r):
    codes = ",".join(f"{k}:{v}" for k, v in r["by_code"].items())
    print(
        f"  {profile_name:<13}  n={r['n']:>3}  c={r['conc']:>3}  "
        f"{r['throughput']:>6.1f} r/s  "
        f"p50={r['p50']:>6.0f}ms  p95={r['p95']:>6.0f}ms  p99={r['p99']:>6.0f}ms  "
        f"max={r['max']:>6.0f}ms  [{codes}]"
    )


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("MS_BASE_URL", "http://localhost:18088"))
    ap.add_argument("--key", default=os.environ.get("MS_API_KEY"))
    ap.add_argument("--only", action="append", help="run only this profile (repeatable)")
    ap.add_argument("--skip", action="append", default=[], help="skip this profile (repeatable)")
    ap.add_argument("--num", type=int, help="override total request count for all profiles")
    ap.add_argument("--concurrency", type=int, help="override concurrency for all profiles")
    args = ap.parse_args()

    if not args.key:
        ap.error("MS_API_KEY required (env var or --key)")

    # Each profile has its own sensible defaults: reads are cheap so run more,
    # writes are slow so run fewer. CLI override beats these.
    defaults = {
        "health":      (200, 50),
        "auth-me":     (200, 50),
        "episodes-ls": (200, 50),
        "search":      (60,  20),
        "write-async": (40,  10),
        "write-sync":  (20,   5),
    }

    only = set(args.only) if args.only else None
    skip = set(args.skip)

    headers_auth = {"X-API-Key": args.key, "Content-Type": "application/json"}

    print(f"  target: {args.base}")
    print(f"  format: profile  n=total  c=concurrency  throughput  latencies  [statuses]")
    print()

    async with httpx.AsyncClient(base_url=args.base.rstrip("/"), http2=False) as client:
        # Warm-up: one round to ensure caches / first connection don't pollute p50.
        await client.get("/v1/health", timeout=10.0)

        for p in PROFILES:
            if only and p.name not in only:
                continue
            if p.name in skip:
                continue
            n_default, c_default = defaults.get(p.name, (100, 20))
            n = args.num or n_default
            c = args.concurrency or c_default
            headers = headers_auth if p.auth else {}
            r = await run_profile(client, p, num=n, concurrency=c, headers=headers)
            print_row(p.name, r)

    print()
    print("  ⓘ  'r/s' is sustained throughput at the given concurrency.")
    print("  ⓘ  Sync writes are LLM-bound; bump concurrency past 5-10 and you'll just queue.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
