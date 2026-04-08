# I built a persistent memory layer for Claude Code and benchmarked it. Results: -19% cost, -18% time, and it stopped making a critical mistake.

## The problem

Claude Code has no memory between sessions. Every time you start a new conversation, it's a fresh hire who knows nothing about your project's history, constraints, or past mistakes. For small projects this is fine. For a 2000+ file legacy Java codebase with 25 years of accumulated "tribal knowledge"? It's painful.

## What I built

**Temper** — a standalone MCP server (Rust) that gives Claude Code persistent memory:

- **Code Graph**: tree-sitter AST analysis, function-level dependency tracking, incremental updates
- **Module Registry**: define module boundaries with glob patterns, auto-suggest on init
- **Knowledge Store**: SQLite with causal chains (A triggers B because C), experience records (symptom→cause→fix), full temporal history
- **Semantic Search**: external embedding API + cosine similarity (keyword fallback when no API key)

It runs as `temper serve` over stdio. Claude Code calls it like any other MCP tool.

## The benchmark

10 identical tasks, run with and without Temper. **Critical design choice**: all constraints and team knowledge were stored ONLY in Temper's memory, not in code comments. This simulates the real world — most "don't do X because Y happened in 2023" rules live in people's heads, not in the code.

25 Java files, 5 modules, tested on Claude Opus 4.6.

## Results

```
                    Without     With Temper    Delta
Total Cost          $1.12       $0.91         -19%
Total Time          241s        197s          -18%  
Output Tokens       6,600       6,133         -7%
Constraint Score    72%         86%           +14%
```

## The interesting tests

### Test that showed the biggest gap: "Add a cache to UserDAO"

Background: This DAO had a Hibernate L2 cache once. It caused stale reads in multi-node deployment (real production incident). The team removed it and agreed to never cache at DAO layer. But this rule was NOT in any code comment.

**Without Temper**: Claude spent 6 turns actively trying to add the cache. Hit max_turns. Never warned about the constraint. Would have reintroduced a known production bug. $0.10 wasted.

**With Temper**: 3 turns. Immediately said "There's an explicit constraint: do NOT add caching to DAO layer. Hibernate L2 cache caused stale reads in multi-node deployment. I'd recommend adding cache at Service layer instead." $0.07.

### Test that showed the biggest efficiency gain: "Add email notification"

**Without Temper**: 6 turns, 59.4 seconds, $0.19. Had to search multiple files to discover the existing notification pattern (TemplateEngine → EmailSender).

**With Temper**: 4 turns, 16.8 seconds, $0.10. Already knew the pattern from module context. **3.5x faster, 47% cheaper.**

### Tests where Temper didn't help

When constraints were inferable from the code itself (e.g., TokenManager already uses SecureRandom — Claude figured out it shouldn't be simplified), both modes performed equally. Temper's value is specifically for knowledge that ISN'T in the code.

## Architecture notes for anyone interested

- **Rust** for performance (tree-sitter is native Rust, sub-second scanning)
- **SQLite** for knowledge store (causal relations as graph edges, temporal history for every state change, no external DB needed)
- **JSON-RPC over stdio** for MCP protocol
- **Glob patterns** for module file matching (ripgrep-style)
- Full temporal history: knowledge is never "forgotten", only marked stale/validated/expired
- Designed with a `KnowledgeStore` trait for future central server mode (team-shared memory)

Core philosophy: **precise persistent memory, not simulated human memory**. No decay, no forgetting, no fuzzy recall. What's recorded is exact. Knowledge can only be marked stale (needs verification) or expired (explicitly by user).

## What I expect on real projects

This was a 25-file toy project. Claude can grep 25 files trivially. On our production codebase (2000+ Java files, 25 years old), search noise grows exponentially. I expect:

- Cost savings: -30% to -50%
- Constraint awareness: +40% to +60%  
- Feature implementation: 5-10x faster

Will run the production benchmark next week and post results.

## Repo

[Link when published]

Happy to answer questions about the architecture or benchmark methodology.

---

**Edit**: To be clear about what this is NOT — it's not RAG over code (Claude tried that and abandoned it because code changes too fast and vectors go stale). Temper stores *knowledge about code* (why it's designed this way, what constraints exist, what broke before), not the code itself. That knowledge changes slowly and doesn't go stale the same way.
