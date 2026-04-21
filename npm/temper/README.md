# @aion0/temper

Constraint lifecycle for source code — place, verify, and monitor `TEMPER-CONSTRAINT` comments so AI coding agents (and humans) can't quietly undo the lessons your team has already paid for.

## Install

```bash
npm install -g @aion0/temper
```

Supported platforms: macOS arm64. Other platforms will need to build from source (see [cargo install](#install-from-source)).

## Why

Claude Code forgets every session. Four rounds of A/B benchmarking (48 `claude -p` runs) showed the only integration path that reliably shaped Claude's behavior was a structured comment embedded in the source file itself — producing +75pp WIN rate across three unrelated constraint types.

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

Claude reads the file when it edits the file — that's the one channel it can't ignore. Temper manages the rest of the lifecycle.

## Commands

```
temper check [PATH] [--staged] [--format text|json]
  Verify every TEMPER-CONSTRAINT in the project.
  Five status classes: OK / DANGLING / CONTRADICTED / BANNED-TOKEN-IN-CODE / STALE.
  Exits non-zero if any problems are found.

temper constraint list [PATH]
  List every TEMPER-CONSTRAINT block with file:line and title.

temper constraint add --target FILE --incident TEXT \
                      [--detail TEXT] [--apply] [--model MODEL]
  Draft a 4-part constraint (What / Why / Rule / Escape) via `claude -p`.
  Stamps today's Last-Verified. --apply inserts above the first class
  declaration.

temper hook install | uninstall
  Install or remove a git pre-commit hook that runs `temper check --staged`
  and blocks commits containing stale / dangling / contradicted constraints.

temper config
  Print the active configuration.
```

## Typical new-project flow

```bash
cd /path/to/your-project

# For each high-risk file (5–20 hotspots per project is normal):
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

Requires `git` on PATH. The `constraint add` subcommand additionally needs `claude` (Claude Code CLI) on PATH.

## Install from source

```bash
git clone https://github.com/aiwatching/temper
cd temper
cargo install --path .
```

## Status

Version 0.2.0 is a product pivot from v0.1.x — see [RELEASE_NOTES.md](https://github.com/aiwatching/temper/blob/main/RELEASE_NOTES.md) for the full story of why the MCP server, code graph, and tree-sitter machinery were removed.
