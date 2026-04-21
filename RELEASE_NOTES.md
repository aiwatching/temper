# Temper v0.2.0

Released: 2026-04-20

## Scope change

v0.2.0 is a **product pivot**, not an incremental update. The MCP server,
code graph, tree-sitter parsers, module registry, HTML export, and CLI
query commands (`impact`, `search`, `call-tree`, `risk`, `overview`,
`diff`, `sync-check`, `dead-code`, `boundary`, `cohesion`, `stats`, `ui`,
`upgrade`, `sync`) are gone.

Temper is now a single-purpose tool: manage the lifecycle of
`TEMPER-CONSTRAINT` comments in source code.

## What changed and why

Four rounds of A/B benchmarking (48 `claude -p` runs, see `benchmark/`)
showed that the only integration vector that reliably shaped Claude's
behavior was a structured comment in the source file the model was about
to edit. MCP tools were ignored. Injected bash output was treated as
noise. CLI query commands over-matched and hurt efficiency. Constraint
comments produced +75pp WIN rate across three unrelated constraint types.

The shrink:

```
Lines of code:      ~6100  →  ~1400   (−77%)
Dependencies:        200+  →   ~30    (−85%)
Binary size:         19MB  →  3.1MB   (−84%)
Build time:          ~30s  →    6s    (−80%)
Top-level commands:    9   →    4     (−56%)
```

## New in v0.2.0

- `temper check` — five-state constraint validation.
- `temper check --staged` — scope to `git diff --cached`, used by hook.
- `temper constraint add` — drafts a 4-part constraint via `claude -p`.
- `temper constraint list` — enumerate all constraints in the project.
- `temper hook install` / `uninstall` — pre-commit guard.
- File-scoped ban-lexicon check.
- Git-timestamp staleness check via `Last-Verified:` header.

## Removed

- MCP server, knowledge store, code graph, tree-sitter parsers, module
  registry, HTML export, embedding client.
- Dependencies: rusqlite, git2, tokio, reqwest, tree-sitter*, bincode,
  globset, uuid.

## Breaking changes

CLI surface is almost entirely different. Scripts using the old commands
need to be rewritten or stay on v0.1.x.

`.temper/knowledge.db`, `graph.json`, and `modules/*.yaml` are no longer
read or produced. Remove the directory if you don't need it.

The MCP auto-registration in `.mcp.json` from old `temper init` is gone.
Remove any `temper` entry in `.mcp.json` manually if upgrading.

## Benchmarks

Round-by-round results: `benchmark/results-constraint-summary-20260420.md`.

## Full changelog

https://github.com/AiON0/temper/compare/v0.1.6...v0.2.0
