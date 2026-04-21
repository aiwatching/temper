# Reflection on Temper v0.2.0

**Date:** 2026-04-21
**Status:** Project paused.

This note records what was learned across the v0.1.x → v0.2.0 arc so that
whoever picks this up later (including future-me) doesn't retrace the same
failed paths. It is deliberately not an advertisement for what works.

---

## What the project set out to do

Build an external memory layer for Claude Code so it wouldn't lose key
constraints across sessions (and within long sessions), especially on
large legacy codebases.

Triggered by a concrete pain point: during the development of a separate
side project, Claude repeatedly forgot two-state-machine rules and kept
reverting fixes. The pattern recurred on other projects. Building an
external memory layer seemed like the obvious fix.

## What got built

**v0.1.x** — MCP server with tree-sitter code graph, SQLite knowledge
store, 17 MCP tools, module registry, embedding-based dedup, HTML
dashboard. ~6000 lines of Rust.

**v0.2.0** — Stripped everything above down to a single focused tool:
scan source files for `TEMPER-CONSTRAINT` comment blocks and validate
them against the current codebase. ~1400 lines.

Commands that remain:
- `temper check [--staged] [--format text|json]`
- `temper constraint list | add --target FILE --incident TEXT [--apply]`
- `temper hook install | uninstall`
- `temper config`

Five validation states per constraint: OK, DANGLING, CONTRADICTED,
BANNED-TOKEN-IN-CODE, STALE (+ UnverifiedAge warning).

## What was validated in 48 `claude -p` A/B runs

| Scenario type | Baseline | With constraint comment | Δ |
|---|---|---|---|
| Direct violation | 0% refusal | 87-100% refusal | +75 to +100 pp |
| Rules-lawyer bypass (DTO, WeakReference, timeout) | 12.5% | 87.5% | +75 pp |
| Legitimate request | 100% implement | 100% implement | 0 (no false positives) |
| Stale constraint (code drifted, comment didn't) | — | Claude blindly obeys, sometimes tries to migrate working code to comply | — |

The +75pp figure held across three unrelated constraint types (data
freshness, thread semantics, architectural layering) and survived
adversarial rules-lawyer prompts. It is the most solid claim this
project produced.

The stale-constraint finding was the most important negative result:
constraint comments without a maintenance loop are not neutral — they
actively harm when the code changes but the comment doesn't. This is
why `temper check` and the pre-commit hook exist.

## Four integration paths that failed, in order

1. **MCP tool layer** — Claude ignored the tools even with `CLAUDE.md`
   instructions to use them. MCP doesn't coerce tool adoption; the model
   picks its own strategy and grep is often its first choice.

2. **Hook injection** — PreToolUse hook rewrote bash commands to append
   temper output alongside grep results. Claude treated the extra content
   as noise and only read the grep portion.

3. **CLI query layer** (`temper impact`, `temper search`) — worked on
   queries where the identifier was unique (8 turns vs 17), hurt on
   common prefixes like "Entity" where the tool returned 157 fuzzy
   matches and Claude wasted 4-6 turns trying to filter before falling
   back to grep. Net: +87% cost, +95% duration on the realistic case.

4. **Source code comments** — worked. Claude reads the file it edits.

All four are in the benchmark/ directory with full traces and scoring
scripts.

## The mistake that became obvious only at the end

Path-scoped rules (e.g. "every file under `masterloader/service/` is
frozen") are already a first-class feature of Claude Code via
`.claude/rules/*.md` with `paths:` frontmatter. Toward the end of this
arc I proposed building a Temper layer to manage those files, which was
just a middleman around functionality Claude Code already ships.

The right split is:

| Scope | Mechanism | Owner |
|---|---|---|
| File-level ("this class must not...") | Source code comment | Temper |
| Path/directory-level | `.claude/rules/*.md` | Claude Code (native) |
| Project-level ("this is how we work") | `CLAUDE.md` | Claude Code (native) |

Temper's legitimate slot is only the first row. Anything else is
duplicating Claude Code.

Had I surveyed Claude Code's `.claude/` conventions on day one, the v0.1
MCP memory layer and the CLI query work would not have happened. The
lesson: **before building external tooling for Claude Code, exhaust what
the harness already provides.**

## What's unresolvable at the tooling layer

Two classes of problem surfaced that no tool built here could fix:

1. **Long-context forgetting.** Within a single session, rules stated
   early get compressed out as context grows. External comments help on
   files Claude re-reads, but a behavioral rule like "ask before
   writing code" has no file to attach to, and `CLAUDE.md` gets
   forgotten in long sessions too.

2. **Stale constraint compliance.** When a written rule no longer
   matches the code, Claude sides with the written rule and may migrate
   working code to comply. Temper detects three kinds of staleness
   (dangling symbols, banned tokens still in code, git-timestamp drift)
   but cannot judge whether the rule's *intent* is still valid — that
   is a human decision.

Both are model-architecture limits, not engineering gaps. Future models
will push them further but are unlikely to fully remove them.

## Why the project is paused

Three reasons converge:

1. **The defensible scope is too narrow to justify continued
   investment.** After the overlap with `.claude/rules/` becomes
   apparent, Temper's unique value is "source code inline constraints
   with staleness checking." That is useful but not a platform-sized
   product.

2. **No clear new idea.** The obvious extensions (from-commit,
   from-postmortem, from-chat) add authoring convenience but do not
   change the fundamental ceiling. Without a hypothesis that would
   meaningfully shift behavior, more features are diminishing returns.

3. **Usage before iteration.** v0.2.0 has not been used on a real
   project for a sustained period. Adding more features now without
   lived experience of what breaks in practice is premature.

## What's left in a usable state

- `v0.2.0` is published at `https://github.com/aiwatching/temper/releases/tag/v0.2.0`.
- `@aion0/temper-darwin-arm64@0.2.0` binary is built and committed to
  the npm package directory but has not been pushed to the npm registry
  (requires `npm login` + `npm publish --access public`).
- `cargo install --path .` from the repo installs to `~/.cargo/bin/temper`.
- `benchmark/` contains every trace file and score, reproducible.
- `publish.sh` is updated for the v2 release flow.

## If the project restarts, the things to test first

Without investing in new code:

1. Use `v0.2.0` on a real project for at least a month. Document every
   time the tool helped, hurt, or was ignored. The design decisions
   after this point should come from that log, not from speculation.

2. Before adding any new feature, verify Claude Code does not already
   provide it. Specifically: `.claude/rules/`, `CLAUDE.md` hierarchy,
   slash commands, hooks, `settings.json` permissions. The failure to
   do this up front cost weeks of work that v0.2.0 then deleted.

3. If the memory problem still feels worth attacking: the interesting
   unexplored direction is not better tooling around Claude Code. It is
   either a different harness (self-built agent with enforced tool
   call) or a different model interface (e.g. the file-based memory
   tool Anthropic is shipping). Both are out of scope for a CLI like
   Temper.

## One-line takeaway

> **A constraint comment in source code, maintained by a pre-commit
> check, reliably shapes Claude's behavior on the file it guards
> (+75pp). Everything else we tried either duplicated Claude Code or
> hit the same model-level ceiling. The project is paused until usage
> data or a new idea justifies resumption.**
