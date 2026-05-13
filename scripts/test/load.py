#!/usr/bin/env python3
"""Light concurrent load test against /v1/auth/me.

Useful for catching:
- API key auth path performance regressions
- DB session leaks (run with -n 500 + watch lsof)
- Async deadlocks

Usage:
    API_KEY=mk_... python3 scripts/test/load.py -n 200 -c 20
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import time

import httpx


async def one_call(client: httpx.AsyncClient, url: str, headers: dict) -> tuple[int, float]:
    t0 = time.perf_counter()
    r = await client.get(url, headers=headers, timeout=10.0)
    return r.status_code, (time.perf_counter() - t0) * 1000.0


async def worker(
    name: int,
    sem: asyncio.Semaphore,
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    results: list[tuple[int, float]],
) -> None:
    async with sem:
        try:
            results.append(await one_call(client, url, headers))
        except Exception as exc:
            results.append((0, 0.0))
            print(f"  worker {name} crashed: {exc}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--num", type=int, default=200, help="total requests")
    ap.add_argument("-c", "--concurrency", type=int, default=20, help="concurrent in-flight")
    ap.add_argument("--base", default=os.environ.get("BASE", "http://localhost:8000"))
    ap.add_argument("--path", default="/v1/auth/me")
    args = ap.parse_args()

    api_key = os.environ.get("API_KEY")
    if not api_key:
        ap.error("API_KEY env var required")

    url = args.base.rstrip("/") + args.path
    headers = {"X-API-Key": api_key}

    print(f"hitting {url} with {args.num} requests, concurrency={args.concurrency}")

    sem = asyncio.Semaphore(args.concurrency)
    results: list[tuple[int, float]] = []

    start = time.perf_counter()
    async with httpx.AsyncClient() as client:
        await asyncio.gather(
            *(worker(i, sem, client, url, headers, results) for i in range(args.num))
        )
    elapsed = time.perf_counter() - start

    by_code: dict[int, int] = {}
    latencies = []
    for code, ms in results:
        by_code[code] = by_code.get(code, 0) + 1
        if code:
            latencies.append(ms)

    print()
    print(f"  total time: {elapsed:.2f}s")
    print(f"  throughput: {args.num/elapsed:.1f} req/s")
    print(f"  status codes: {dict(sorted(by_code.items()))}")
    if latencies:
        latencies.sort()
        print(f"  latency ms:")
        print(f"    min:  {min(latencies):.1f}")
        print(f"    p50:  {statistics.median(latencies):.1f}")
        print(f"    p95:  {latencies[int(len(latencies)*0.95) - 1]:.1f}")
        print(f"    p99:  {latencies[int(len(latencies)*0.99) - 1]:.1f}")
        print(f"    max:  {max(latencies):.1f}")

    success = by_code.get(200, 0)
    if success != args.num:
        print(f"\nFAILED: {args.num - success}/{args.num} requests did not 200")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
