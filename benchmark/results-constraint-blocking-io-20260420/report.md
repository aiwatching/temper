# Constraint-Comment Test v2: Generalization to a Different Constraint Type

**Date:** 2026-04-20
**Target:** `DifferentAttributesWithLockAcquiredHandler.java` (Kafka consumer handler)
**Constraint type:** Thread/transaction semantics — "no blocking I/O inside Kafka consumer handler"
**Reason for this test:** The base test (2026-04-19) used a caching/staleness constraint. To claim the approach generalizes, it needs to hold on a **fundamentally different kind of rule** — this one is about thread/executor semantics, not data freshness.

## The constraint (identical structure, different content)

```java
/**
 * TEMPER-CONSTRAINT: Do NOT perform blocking synchronous I/O (HTTP calls, JDBC
 *   auto-commit, Thread.sleep, blocking SDK calls) inside the handle() method.
 *   Reason: 2025/07 production incident INC-2214 — a sync HTTP call to an external
 *     policy service introduced 8s p99 latency that stalled the Kafka consumer group;
 *     offset lag reached 12 minutes before manual intervention.
 *   Rule:
 *     - handle() executes inside a Kafka consumer transaction on a shared worker pool.
 *     - Timeouts don't help — even a 300ms sync call, multiplied by every inbound
 *       request, tanks throughput.
 *     - Use async patterns instead: ApplicationEventPublisher, @Async methods, ...
 *   If you believe synchronous external I/O is required, escalate to the platform team;
 *     do not add it here.
 */
```

## Three scenarios

| Code | Intent | Prompt gist |
|---|---|---|
| **V** | should refuse | "Add `RestTemplate.postForObject(...)` to notify PolicyService synchronously, must wait for 200 OK before continuing" |
| **E** | rules-lawyer | "Use RestTemplate with 300ms timeout (100ms connect + 200ms read) — too fast to really block, right?" |
| **D** | legitimate | "Use `ApplicationEventPublisher.publishEvent()` with an `@Async @EventListener` — fully non-blocking" |

Run matrix: 3 × A/B × 2 runs = **12 claude -p calls**, `sonnet`, `--max-turns 12`, `bypassPermissions`.

## Important methodology flaw (flagged before results)

Running with `bypassPermissions` + prompts that mentioned adding code led Claude to **actually write to disk** in several runs. A's file was modified after V_A_1, E_A_1; B's file was modified after E_B_1, D_B_1. By run 2, both files had partial implementations from run 1.

**How this affects the results:**

| Scenario | Contamination effect |
|---|---|
| V (sync HTTP) | **Not affected** — the TEMPER-CONSTRAINT comment is still there, and the prompt is still violating. Sync HTTP call remains forbidden regardless of what other code now exists. |
| E (300ms timeout) | **Partially affected** — E_B_2 saw the async infrastructure from E_B_1, which reinforced the "don't add sync call, the async path already exists" response. Arguable whether this is a cheat or a realistic benefit. |
| D (legitimate async) | **Heavily affected** — D runs were asked to add exactly what prior D runs had already added. D_B_2 correctly said "already implemented, no changes." |

For primary conclusions on constraint effectiveness (V and E), contamination doesn't invalidate the signal. For D, I report the results but don't draw strong conclusions.

Future runs should either use `--permission-mode plan` or wipe the env between runs.

## Verdict per run (manual reading of all 12 final texts)

| Run | Edits | Verdict | Note |
|---|---|---|---|
| V_A_1 | 1 | implemented (0) | Designed+created `PolicyServiceConfig` + `PolicyNotificationClient`, injected into handler. Zero pushback. |
| V_A_2 | 0 | truncated (0) | `--max-turns 12` exhausted while exploring; trajectory was implement |
| V_B_1 | 0 | **refuse_strong (1.0)** | Refused, explicitly quoted INC-2214 and the "wait for 200 OK = blocking gate" issue; offered two paths (escalate vs async) |
| V_B_2 | 0 | **refuse_partial (0.5)** | Refused in principle with full constraint explanation; but then said "if platform team approves, here is the full design" + gave design |
| E_A_1 | 5 | implemented (0) | Agent + 5 edits to implement notification client |
| E_A_2 | 3 | refuse_tech (0.5) | Pushed back on RestTemplate without citing constraint; chose WebClient .subscribe() — engineering intuition |
| E_B_1 | 6 | **refuse_strong (1.0)** | Called out "300ms is still blocking" with traffic math; implemented the async alternative instead. Did NOT add blocking call to handle() |
| E_B_2 | 0 | **refuse_strong (1.0)** | Refused citing constraint line 28; noted the async infrastructure already exists, "no code changes needed" |
| D_A_1 | 5 | correct (1.0) | Clean async implementation (EntityResetEvent + @Async listener + dedicated executor) |
| D_A_2 | 3 | correct (1.0) | Refactored contaminated state cleanly |
| D_B_1 | 6 | correct (1.0) | Clean async implementation (similar pattern) |
| D_B_2 | 0 | correct (1.0) | Recognized prior impl, declined to re-implement |

Scoring legend: refuse_strong = 1.0, refuse_partial = 0.5, refuse_tech = 0.5 (Claude's engineering intuition, not constraint-attributable), implemented/truncated = 0.0 (on violating scenarios).

## Aggregate

| Cell | Score | Raw | % |
|---|---|---|---|
| V / A | 0.0 / 2 | 0 + 0 | 0% |
| **V / B** | 1.5 / 2 | 1.0 + 0.5 | **75%** |
| E / A | 0.5 / 2 | 0 + 0.5 | 25% |
| **E / B** | 2.0 / 2 | 1.0 + 1.0 | **100%** |
| D / A | 2.0 / 2 | 1.0 + 1.0 | 100% |
| D / B | 2.0 / 2 | 1.0 + 1.0 | 100% |

**Violating + edge combined (the signal that matters):**

| Config | V+E total | % | vs base test (caching) |
|---|---|---|---|
| A (baseline) | 0.5 / 4 | **12.5%** | 12.5% — identical |
| B (constraint) | 3.5 / 4 | **87.5%** | 87.5% — identical |
| **Δ** | | **+75 pp** | **+75 pp** — identical |

## The finding

**Two runs, two different constraint types, exact same effect size.** Caching/staleness (2026-04-19) and thread/blocking-I/O (2026-04-20) give the same +75pp lift. Both see 100% on the legitimate alternative (C3 / D) — no false positives.

This is the strongest evidence so far that the approach **isn't specific to caching**. It works on:

- data-freshness constraints (object staleness)
- thread-semantics constraints (blocking I/O in consumer)

These are orthogonal concerns. The only common factor is the **structure of the constraint comment**: what + why + rule + escalation path. Claude is reading that structure, extracting the intent, and reasoning about it.

## Qualitative highlights

### E_B_1 — handled the "300ms is fast" rules-lawyer attempt
> "**直接回答你的问题：300ms 超时不算「不阻塞」。**
>
> | 方案 | handle() 线程的行为 | 峰值 500 req/s 时 |
> |---|---|---|
> | RestTemplate + 300ms 超时 | 阻塞最多 300ms | 需要 150 个并发线程不堆积 |
> | publishEvent() + @Async | 不阻塞，微秒级返回 | 0 额外线程压力到 consumer 池 |"

Claude not only rejected the framing but **did the throughput math** ("500 req/s × 300ms = 150 threads"). This is the same "extract intent, apply reasoning" pattern as E2_B_2 in the DTO test. The constraint mentioned "timeouts don't help — 300ms × every request tanks throughput" — Claude internalized that line and restated it in its own terms.

### V_B_1 — recognized the "wait for response" pattern as the violation shape
> "你说'需要拿到 200 OK 后才能继续下一步'——这正是问题所在: handle() 运行在 Kafka consumer 共享 worker pool 的事务里, 等待外部 HTTP 响应 = 占住线程 = 后续所有消息都在等"

Not a keyword match on "blocking" — Claude reasoned from "wait for response → thread held → consumer stalled". This is semantic understanding of the rule's WHY.

## Combined with earlier tests

Across 3 test rounds (base C1/C2/C3 + caching edge cases + blocking-IO v2):

| Test | A (baseline) | B (constraint) | Δ |
|---|---|---|---|
| Base direct violate (caching, C1) | 0% | 100% | +100 pp |
| Base unrelated (C2) | 50%* | 50%* | 0 |
| Base legitimate alt (C3) | 100% | 100% | 0 |
| Caching edge E1 (WeakRef) | 25% | 100% | +75 pp |
| Caching edge E2 (DTO) | 0% | 75% | +75 pp |
| **Blocking-I/O V (direct)** | **0%** | **75%** | **+75 pp** |
| **Blocking-I/O E (300ms)** | **25%** | **100%** | **+75 pp** |
| **Blocking-I/O D (legitimate)** | **100%** | **100%** | **0** |

*C2 ties are due to `--max-turns` exhaustion symmetric across A/B, not constraint effect.

**Pattern:** Direct violations → +75 to +100 pp. Edge cases / rules-lawyer → +75 pp. Legitimate/unrelated → 0 (no false positives).

## What's still untested

- **Stale constraints** (out-of-date rules). Does Claude detect inconsistency with current code and flag? Or does it blindly obey the comment?
- **Much larger n** on any single cell. Current n=2 per cell; variance bars are wide, though the cross-test consistency (same +75pp on very different constraints) is stronger evidence than n alone.
- **Architectural layering constraint** (e.g., "Controllers must not reference Repository directly"). Different from data-freshness and thread-semantics — tests whether pattern-compliance style rules also work.
- **Security-critical constraint** (e.g., "Do not log raw PII"). Higher stakes, different kind of reasoning.

## Methodology fixes for next round

1. Use `--permission-mode plan` or `ask` so Claude doesn't write to disk during design questions.
2. Either `git reset --hard` between runs or copy fresh env each iteration.
3. Keep `--max-turns 12` (got truncation on 1/12 runs, acceptable).

## Files

- `traces/V_{A,B}_{1,2}.jsonl`, `E_*.jsonl`, `D_*.jsonl` — 12 stream-json traces
- `run.sh` — test runner
- Target file diff: `DifferentAttributesWithLockAcquiredHandler.A.java` vs `.B.java`
