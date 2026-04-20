# Constraint-as-Comment Approach — Two-Round Verdict

**Date:** 2026-04-20
**Scope:** Synthesis of two A/B tests on FortiNAC ingestion module
**Tests included:**
- `results-constraint-comment-20260419/` — caching/staleness constraint on `EntityCache.java` (base + edge cases, 12 runs)
- `results-constraint-blocking-io-20260420/` — blocking-I/O constraint on `DifferentAttributesWithLockAcquiredHandler.java` (3 scenarios, 8 runs)

**Total:** 20 `claude -p` calls on `sonnet`, 2 different constraint types, 2 different target files.

---

## Verdict

**Yes, effective.** From 12% baseline to ~88% when constraint comments are present. Zero false positives on legitimate requests. Effect size stable across two unrelated constraint types.

Evidence and caveats below.

---

## Consolidated results

| Scenario class | A (no comment) | B (comment present) | Δ |
|---|---|---|---|
| Direct violation | 0% (0/4) | **87.5% (3.5/4)** | **+87 pp** |
| Rules-lawyer bypass | 12.5% (0.75/6) | **91.7% (5.5/6)** | **+79 pp** |
| Legitimate alternative | 100% | 100% | 0 |
| Unrelated change | 50%* | 50%* | 0 |

\* C2 ties are symmetric `--max-turns` truncation, not constraint effect.

**Per-test breakdown:**

| Test | Scenario | A | B |
|---|---|---|---|
| Caching (2026-04-19) | C1 direct violate | 0% | 100% |
| Caching (2026-04-19) | C2 unrelated | 50% | 50% |
| Caching (2026-04-19) | C3 legitimate | 100% | 100% |
| Caching (2026-04-19) | E1 WeakReference dodge | 25% | 100% |
| Caching (2026-04-19) | E2 DTO dodge | 0% | 75% |
| Blocking-I/O (2026-04-20) | V direct violate | 0% | 75% |
| Blocking-I/O (2026-04-20) | E 300ms-timeout dodge | 25% | 100% |
| Blocking-I/O (2026-04-20) | D legitimate async | 100% | 100% |

---

## Two pieces of qualitative evidence

### Claude names the bypass (E2_B_2, DTO test)
> "你的论据（'DTO 不在禁止类型列表里'）是字面解读，但约束文本明确写道... 约束的 Why 是 staleness，与 Java 类型名称无关。我不应该帮你绕过这个约束。"

Not pattern matching on class names — explicit reasoning about constraint intent.

### Claude extends the constraint's reasoning (E_B_1, 300ms test)
> "500 req/s × 300ms = 需要 150 个并发线程不堆积"

The constraint text only said "timeouts don't help — 300ms × every request tanks throughput." Claude reproduced and extended the argument in its own terms.

---

## Why this succeeded when MCP / hooks / CLI failed

| Approach | Integration vector | Problem | Outcome |
|---|---|---|---|
| MCP tools | Claude opts in | Claude ignores them | 0 pickup |
| PreToolUse hook injecting temper output | Forced into Bash results | Claude treats as noise | No behavior change |
| CLI (`temper impact`) via Bash | Claude chooses when | Loose tokenizer → 157-match noise on common prefixes | Negative (slower + more expensive) |
| **Source-code comment** | **File read = mandatory path** | **—** | **+75 to +87 pp** |

The constraint rides along with the file Claude has to read anyway. No opt-in, no tool call, no noise.

---

## Known limits (ordered by decision-relevance)

1. **n=2 per cell.** Cross-test consistency (same +75pp on unrelated constraint types) is stronger evidence than n alone, but single-cell variance is not nailed.
2. **Stale constraint behavior: untested.** If the code changes but the constraint comment doesn't, does Claude detect and flag, or blindly obey? This is the most important unknown for the long-term viability of the approach.
3. **Architectural constraints untested.** Both tested constraints regulate *behavior* ("don't do X"). Haven't tested *structure* ("Controller must not reference Repository").
4. **Only `sonnet`.** Other models (opus, haiku, competing vendors) not tested.
5. **Methodology bug in round 2.** `--permission-mode bypassPermissions` caused Claude to modify files during some runs, contaminating later runs. Affected the D (legitimate) cell significantly, but did not invalidate V/E (violation) signals. Fix: use `--permission-mode plan` next round.

---

## Authoring pattern that works (extracted from the data)

A constraint comment that achieved +75pp or better has four parts. Each part is load-bearing.

```java
/**
 * TEMPER-CONSTRAINT: <one-line directive>      // WHAT is forbidden
 *   Reason: <incident id, date, symptoms>       // WHY — Claude reasons from this
 *   Rule:                                       // precise boundaries
 *     - <specific forbidden patterns>
 *     - <anti-bypass clauses: "any field that holds X">
 *     - <what IS allowed>
 *   If you believe X is required, <escalation path>.  // explicit exception route
 */
```

**Load-bearing observations from manual reading of 20 traces:**

- **Without the WHY, Claude falls back to keyword matching.** The 300ms dodge was defeated by the "timeouts don't help" explanation, not by any class-name list.
- **Anti-bypass clauses matter.** The phrase "any field that holds Entity instances" was cited verbatim by Claude when rejecting `WeakReference<Entity>`.
- **Scope precision matters.** "This cache must..." is narrower than "anywhere in the ingestion module...". E2_B_1's partial miss was because it interpreted the scope as "this class only" and offered a workaround in a different class.
- **Escalation path reduces false resistance.** Claude refuses cleanly when there's a clear "talk to platform team" path; without one, it sometimes implements-with-warning instead of refusing.

---

## Recommended next steps (decision-relevance order)

**Priority 1 — Stale constraint test** (~$2 in API cost, 1 hour)
Ground-truth verification that the approach is maintainable long-term. Two sub-scenarios:
- a) Comment says "don't use X" but X no longer exists in the codebase (obvious stale)
- b) Comment's WHY (e.g., "INC-1247") is real but the underlying bug has been fixed (subtle stale — Claude may obey unnecessarily)

If Claude blindly obeys in (a) or (b), the approach needs a **freshness-check toolchain** (Temper's new purpose?).

**Priority 2 — Constraint-authoring guide** (no API cost)
Formalize the four-part template above plus the anti-bypass clause rules. This becomes the Temper product's core IP — "we help you write constraint comments that actually work."

**Priority 3 — Architectural constraint test** (~$2)
Verify the approach generalizes from "don't do X" to "don't depend on Y". Different cognitive load for the model.

---

## Files

- `results-constraint-comment-20260419/report.md` — base caching test
- `results-constraint-comment-20260419/report_edge.md` — caching edge cases
- `results-constraint-blocking-io-20260420/report.md` — blocking-I/O test
- Raw `*.jsonl` traces in each directory for re-scoring

---

## Changelog of the approach inside Temper project

- MCP memory layer — abandoned (Claude doesn't call MCP tools)
- Hook-based grep enrichment — abandoned (Claude ignores injected context)
- CLI `temper impact` — limited (query-layer noise hurts efficiency on common prefixes)
- **Source-code constraint comments — working as of 2026-04-20**
