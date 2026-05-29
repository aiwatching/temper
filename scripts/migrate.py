#!/usr/bin/env python3
"""Move per-user memory between TEMPER instances.

Thin client over /v1/me/export + /v1/me/import. Use to bring your
blocks / documents / episodes from one TEMPER deploy to another:

  ./scripts/migrate.py export \\
      --from http://localhost:18088 --token sk-... \\
      --out bundle.json

  ./scripts/migrate.py import \\
      --to http://server:18088 --token sk-... \\
      --file bundle.json [--mode merge|replace] [--background-extraction]

The token is whatever auth header the target API expects — a session
JWT (from /v1/auth/login) or an API key. The same auth header works
for both export and import; we don't try to distinguish.

Uses stdlib only (urllib + json) so this runs anywhere with python3
without needing to install httpx/requests on the source machine.

Run as a module from the repo root:

  python3 scripts/migrate.py export --from ... --token ... --out ...

No project installation required."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any


def _request(
    method: str,
    url: str,
    token: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: int = 600,
) -> Any:
    """One-shot JSON request. 600s default timeout covers slow imports
    (each episode is a sync graphiti add_episode + LLM call)."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        # Print the server's error body — it usually has the useful
        # detail (validation message, auth hint, etc.) buried in JSON.
        sys.stderr.write(
            f"\nHTTP {e.code} from {method} {url}:\n{body_text}\n"
        )
        sys.exit(2)
    except urllib.error.URLError as e:
        sys.stderr.write(f"\nrequest failed: {method} {url}: {e}\n")
        sys.exit(2)


def cmd_export(args: argparse.Namespace) -> None:
    base = args.from_url.rstrip("/")
    print(f"[export] GET {base}/v1/me/export", file=sys.stderr)
    bundle = _request("GET", f"{base}/v1/me/export", args.token)

    counts = {
        "blocks": len(bundle.get("blocks", [])),
        "documents": len(bundle.get("documents", [])),
        "episodes": len(bundle.get("episodes", [])),
    }
    print(
        f"[export] got: {counts['blocks']} blocks, "
        f"{counts['documents']} documents, {counts['episodes']} episodes",
        file=sys.stderr,
    )

    if args.out == "-":
        json.dump(bundle, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(bundle, f, indent=2, ensure_ascii=False)
        print(f"[export] wrote {args.out}", file=sys.stderr)


def cmd_import(args: argparse.Namespace) -> None:
    if args.file == "-":
        bundle = json.load(sys.stdin)
    else:
        with open(args.file, encoding="utf-8") as f:
            bundle = json.load(f)

    base = args.to_url.rstrip("/")
    query = f"?mode={args.mode}"
    if args.background_extraction:
        query += "&background_extraction=true"
    url = f"{base}/v1/me/import{query}"

    counts = {
        "blocks": len(bundle.get("blocks", [])),
        "documents": len(bundle.get("documents", [])),
        "episodes": len(bundle.get("episodes", [])),
    }
    total = sum(counts.values())
    print(
        f"[import] POST {url}\n"
        f"[import] sending {counts['blocks']} blocks, "
        f"{counts['documents']} documents, {counts['episodes']} episodes "
        f"({total} total)",
        file=sys.stderr,
    )
    if not args.background_extraction and counts["episodes"] > 50:
        print(
            f"[import] heads-up: {counts['episodes']} episodes will each "
            "be re-extracted by the target's LLM. This can take a while "
            "(rough ballpark: 2-5s per episode). Consider "
            "--background-extraction if you want the request to return fast.",
            file=sys.stderr,
        )

    report = _request("POST", url, args.token, body=bundle)

    print(json.dumps(report, indent=2, ensure_ascii=False))
    errs = report.get("errors", [])
    if errs:
        sys.stderr.write(f"\n[import] {len(errs)} row(s) failed:\n")
        for e in errs[:10]:
            sys.stderr.write(f"  - {e['kind']} {e['target']}: {e['error']}\n")
        if len(errs) > 10:
            sys.stderr.write(f"  ... and {len(errs) - 10} more.\n")
        sys.exit(1)


def main() -> None:
    p = argparse.ArgumentParser(
        prog="migrate.py",
        description="Move per-user TEMPER memory between hosts.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("export", help="Dump memory bundle to JSON.")
    pe.add_argument(
        "--from", dest="from_url", required=True,
        help="Source base URL, e.g. http://localhost:18088",
    )
    pe.add_argument("--token", required=True, help="Auth token (JWT or API key)")
    pe.add_argument(
        "--out", default="-",
        help="Output path. '-' (default) writes to stdout.",
    )
    pe.set_defaults(func=cmd_export)

    pi = sub.add_parser("import", help="Load bundle into target.")
    pi.add_argument(
        "--to", dest="to_url", required=True,
        help="Target base URL, e.g. http://server:18088",
    )
    pi.add_argument("--token", required=True, help="Auth token (JWT or API key)")
    pi.add_argument(
        "--file", required=True,
        help="Bundle JSON path. '-' reads from stdin.",
    )
    pi.add_argument(
        "--mode", choices=("merge", "replace"), default="merge",
        help="merge (default): upsert; replace: wipe target user first.",
    )
    pi.add_argument(
        "--background-extraction", action="store_true",
        help=(
            "Episode writes return immediately; graphiti extraction "
            "happens in the background. Trades 'graph complete on "
            "import response' for fast bulk-write."
        ),
    )
    pi.set_defaults(func=cmd_import)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
