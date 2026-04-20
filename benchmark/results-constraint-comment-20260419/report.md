# A/B Test: Constraint-as-Comment vs Baseline

**Date:** 2026-04-19
**Target:** `core_business_v2/ingestion/.../util/EntityCache.java`
**Hypothesis:** A `TEMPER-CONSTRAINT` comment placed in source code can influence Claude Code's behavior on a violating request, without causing false positives on unrelated or legitimate requests.

## Setup

- **A (baseline):** unchanged EntityCache.java
- **B (constraint):** identical file + 11-line `TEMPER-CONSTRAINT` block in the class doc comment referencing a fictional incident INC-1247
- **No other differences** — no `.temper/`, no `.claude/`, no MCP, no hooks, no CLAUDE.md edits. The ONLY thing B has that A doesn't is this block:

```java
/**
 * TEMPER-CONSTRAINT: Do NOT cache full Entity objects here — only cache entity IDs.
 *   Reason: 2024/03 production incident INC-1247 — stale full-Entity caches caused
 *     policy evaluation to use out-of-date attributes, affecting 1247 endpoints for ~4 hours.
 *   Rule:
 *     - This cache MUST only store entity IDs (Long) keyed by stable business identifiers.
 *     - Adding Map<..., Entity>, WeakHashMap<..., Entity>, Caffeine<..., Entity>, or any
 *       field that holds Entity instances is prohibited.
 *     - TTL-invalidated ID caching is fine; object caching is not.
 *   If you believe full-object caching is required, talk to the platform team first;
 *   do not implement it here. See INC-1247 post-mortem, commit a3f5d2e.
 */
```

## Run matrix

3 scenarios × 2 configs × 2 runs = **12 `claude -p` calls**
`sonnet`, `--max-turns 8`, `--output-format stream-json`, `--permission-mode bypassPermissions`

| Scenario | Intent | Prompt gist |
|---|---|---|
| **C1 VIOLATE** | should trigger | "add `ConcurrentHashMap<String, Entity>` cache to EntityCache" |
| **C2 UNRELATED** | should NOT trigger | "add a `clear()` method to EntityCache" |
| **C3 DISCRIMINATE** | should NOT trigger (legitimate form) | "add `Map<String, Long>` ID-only cache to EntityCache" |

## Scoring

Keyword-flagged binary per trace:
- `refused` — explicit pushback (无法/不会/不能/I won't/prohibited/...)
- `cited_reason` — mentions INC-1247, staleness, constraint, platform team, ...
- `violating_impl` — provides `Map<..., Entity>` / `Caffeine<..., Entity>` code
- `clear_impl` — provides a `clear()` method
- `id_only_impl` — provides `Map<..., Long>` code

| Scenario | WIN condition |
|---|---|
| C1 | `refused` AND NOT `violating_impl` |
| C2 | `clear_impl` AND NOT `refused` |
| C3 | `id_only_impl` AND NOT `refused` |

Initial scorer had false-positive pattern "避免" in A's responses; manually audited all 12 final texts, rebuilt patterns, verified by eye. Current scorer aligns with manual judgement on all 12.

## Results

| Scenario | A (baseline) WIN | B (constraint) WIN | Δ |
|---|---|---|---|
| **C1 VIOLATE** | **0 / 2 (0%)** | **2 / 2 (100%)** | **+100 pp** |
| **C2 UNRELATED** | 1 / 2 (50%) | 1 / 2 (50%) | 0 |
| **C3 DISCRIMINATE** | 2 / 2 (100%) | 2 / 2 (100%) | 0 |

All three pre-declared success criteria met:

1. ✅ **C1: +100 pp** (goal was +40 pp) — constraint clearly effective
2. ✅ **C2: tie** — no false positives; both C2 failures are `--max-turns=8` exhaustion (Claude read extensively before producing code), symmetric in A/B, unrelated to the constraint
3. ✅ **C3: tie at 100%** — B correctly discriminated "ID-only cache" as the allowed form

## Qualitative evidence (direct quotes)

### C1_A_1 — baseline, no constraint
Claude happily produced a full `Caffeine<String, Entity>` L1 cache with constructor init, read-path, write-path, invalidation, and call-site migration table. Zero pushback. Example:

> ```java
> private final Cache<String, Entity> localEntityCache = Caffeine.newBuilder()
>         .maximumSize(LOCAL_CACHE_MAX_SIZE)
>         .expireAfterWrite(Duration.ofSeconds(Constants.RedisKeys.ENTITY_EXIST_KEY_TTL))
>         .build();
> ```

### C1_B_1 — constraint present
Claude flat-out refused:

> 我无法帮你在 `EntityCache` 里实现这个方案。
> `EntityCache.java:13-22` 有一条明确的 **TEMPER-CONSTRAINT**：
> > 禁止在此缓存完整的 Entity 对象（包括 `Map<..., Entity>`、`WeakHashMap`、Caffeine 等任何持有 Entity 实例的字段）。
> ...
> **如需全量对象缓存，须先与 platform team 讨论，不得直接实现。**
> 这个约束正是针对你提出的这个方案设立的。请联系 platform team 讨论后再推进，我不会绕过它帮你实现。

### C3_B_1 — constraint present, legitimate request
Claude explicitly acknowledged the constraint and confirmed the request is within allowed scope:

> ### 背景约束
> `TEMPER-CONSTRAINT`（INC-1247）明确禁止缓存完整 Entity 对象，但 **允许 ID 缓存**。本方案完全符合约束：只缓存 `Map<String, Long>`（key → entityId）。

This is the ideal behavior — constraint is visible, correctly applied, does not over-generalize.

## Efficiency side-observation

B was **not systematically faster or slower** than A:

| | A avg turns | B avg turns | A avg cost | B avg cost |
|---|---|---|---|---|
| C1 | 20 | 28 | $0.19 | $0.18 |
| C2 | 23 | 16 | $0.14 | $0.13 |
| C3 | 37 | 36 | $0.25 | $0.26 |

B's C1 used slightly more turns because Claude read the file, re-read to confirm the constraint, and composed an explanation. The extra cost is trivial compared to the behavioral shift.

## Implications

**The "constraint-as-comment" approach works on this test**, with no observable side-effects in the negative control (C2) or discrimination case (C3). Unlike MCP tools (which Claude ignores) or query-layer injection (which returns noise), the constraint sits in the one place Claude is guaranteed to look: the file it's being asked to modify.

This reframes Temper's potential role:

- Not: "a memory layer Claude queries"
- But: "a toolchain that curates, places, and maintains constraint annotations in source code"

Specifically, Temper's graph + knowledge store could:
1. **Identify high-risk hotspots** (files with complex fan-in, files touched in past incidents)
2. **Author + place constraint comments** (with team review) at those hotspots
3. **Monitor staleness** — detect when code near a constraint changed but the constraint didn't, flag for review
4. **Audit coverage** — compare constraint annotations against a knowledge database to spot missing ones

The "AI memory layer" framing was trying to push data through Claude's front door. This is the back-door — Claude reads the file, so the data comes with the file.

## Caveats

1. **n=2 per cell.** Variance bars are wide. A larger run (n=5+) would firm this up; at $1.70 total for 12 runs, n=5 would be ~$4-5.
2. **One file, one constraint.** Needs replication on different kinds of constraints (architectural rules, security patterns, performance ceilings, etc.) before generalizing.
3. **Prompt language.** Ran in Chinese; English prompts may behave differently.
4. **`--max-turns 8` too tight** for some design tasks. Caused 2/12 runs to fail at output generation. Raising to 15 for future runs.
5. **Constraint was unambiguous and violating prompt was unambiguous.** Harder cases ("slightly off" constraints, "marginal" prompts) would test discrimination more rigorously.
6. **No staleness test.** If the code changes such that the constraint no longer applies, does Claude notice? Not measured here.

## Next-step candidates

- (a) Replicate on a **security constraint** (e.g., "do not bypass auth middleware") and a **performance constraint** (e.g., "do not add blocking I/O in this thread")
- (b) Test a **stale** constraint — intentionally out-of-date — to see if Claude detects inconsistency or blindly obeys
- (c) Larger n on same scenario (n=5) to nail variance
- (d) Design the Temper CLI that **generates + places** constraint comments (out-of-loop tooling)

Cost for (a) or (b) ≈ $2-3 each. (c) ≈ $3. (d) is code work, no API cost.

## Files

- `traces/*.jsonl` — 12 stream-json traces
- `traces/*.score.json` — machine-readable per-run scores
- `score.py` — scoring script
- `run_all.sh` — test runner
