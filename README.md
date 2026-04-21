# Temper — Constraint Lifecycle for Source Code

Place, verify, and monitor `TEMPER-CONSTRAINT` comments so AI coding agents
(and humans) can't quietly undo the lessons your team has already paid for.

---

## Why

Claude Code forgets every session. MCP tools, hooks, and CLI query commands
all failed to reliably influence its behavior across our 48-run benchmark
suite (see `benchmark/`). The **only** integration path that produced a
consistent +75pp WIN rate across three unrelated constraint types was a
structured comment embedded in the source file itself:

```java
/**
 * TEMPER-CONSTRAINT: Do NOT cache full Entity objects — only cache entity IDs.
 *   Reason: 2024/03 INC-1247, stale caches broke policy eval for 1247 endpoints.
 *   Rule:
 *     - No Map<..., Entity>, WeakHashMap, Caffeine, or any field holding an Entity.
 *     - TTL-invalidated ID caching is fine.
 *   If full-object caching is required, talk to the platform team first.
 *   Last-Verified: 2026-04-20
 */
public class EntityCache { ... }
```

Claude reads the file when it edits the file — that's the one channel it
can't ignore. The constraint sits next to the code it guards, travels
with the codebase, and Temper enforces the rest of the lifecycle.

---

## Commands

```
temper check [PATH] [--staged] [--format text|json]
  Verify every TEMPER-CONSTRAINT in the project (or just staged files).
  Five status classes: OK / DANGLING / CONTRADICTED / BANNED-TOKEN-IN-CODE / STALE.
  Exits non-zero if any problems are found.

temper constraint list [PATH]
  List every TEMPER-CONSTRAINT block found, with file:line and title.

temper constraint add --target FILE --incident TEXT \
                      [--detail TEXT] [--apply] [--model MODEL]
  Ask `claude -p` to draft a 4-part constraint (What / Why / Rule / Escape),
  stamped with today's Last-Verified. Prints the draft; --apply inserts it
  above the first class/interface/struct declaration.

temper hook install | uninstall
  Install or remove a git pre-commit hook that runs `temper check --staged`
  and blocks the commit if any constraint is stale, dangling, contradicted,
  or has a banned token in code.

temper config
  Print the active configuration.
```

---

## Staleness checks

`temper check` runs five checks per constraint and collapses them into a
single status:

| Status | Meaning |
|---|---|
| **OK** | all checks pass |
| **DANGLING** | constraint mentions a class/interface the codebase no longer has |
| **CONTRADICTED** | constraint forbids a generic pattern (e.g. `Map<..., Entity>`) that still appears in code |
| **BANNED-TOKEN-IN-CODE** | "Do NOT use X" prose where X still appears in the constraint's own file |
| **STALE** | file was committed to after its `Last-Verified:` header |
| **NO-LAST-VERIFIED** | no header at all, file untouched > 180 days |

---

## Install

```bash
cargo install --path .
temper --version
```

Requires `git` on PATH (for timestamp and staged-files checks). The
`constraint add` subcommand additionally needs `claude` (Claude Code CLI)
on PATH.

---

## A new-project checklist

```bash
cd /path/to/your-project

# For each high-risk file:
temper constraint add \
  --target src/path/Hotspot.java \
  --incident "INC-1247: cache-Entity-object → stale eval" \
  --detail "attributes update frequently; only cache IDs" \
  --apply

# Sanity check
temper check

# Guard future commits
temper hook install

git add . && git commit -m "guard hotspots with TEMPER-CONSTRAINT"
```

Start with 5–20 hotspot files — anywhere that had an incident, has high
fan-in, or hits thread/transaction/cache boundaries. Don't try to blanket
the whole repo; constraints only pay off on the files where a mistake
would actually recur.

---

## What Temper does not do

- Does not build a code graph, call-tree, or impact analysis (v1 tried;
  Claude did not use the output).
- Does not run a long-lived daemon or synchronize a knowledge database.
- Does not enforce constraints across files the comment doesn't sit
  next to. File-scoped enforcement is deliberate; team-wide policy is a
  different mechanism (lint rule, CI check).
- Does not guarantee Claude obeys — but in 48 controlled runs with four
  different constraint types, it obeyed 87–100% of the time vs ~12% without
  a constraint comment.

---

## Status

- Version: **0.2.0** — constraint lifecycle platform.
- Predecessor (`v0.1.x`) was an MCP memory layer; its code lives on the
  `main` branch before `76a8c30` and can be mined for reference.
- Benchmarks: `benchmark/results-constraint-summary-20260420.md` summarises
  the four rounds that pinned the approach down.
