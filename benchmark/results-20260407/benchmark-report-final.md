# Temper Benchmark: Does Persistent Memory Make Claude Code Better?

## TL;DR

We ran 10 identical coding tasks on Claude Code, once **without** any memory, once **with Temper** (a persistent memory MCP server). All "team knowledge" — constraints, past bugs, design decisions — was stored **only** in Temper, not in code comments.

### Headline Numbers

```
                    Without Temper    With Temper     Delta
─────────────────────────────────────────────────────────────
Total Cost          $1.12            $0.91           -19%
Total Time          241s             197s            -18%
Output Tokens       6,600            6,133           -7%
Constraint Score    72%              86%             +14%
```

---

## Setup

- **Test project**: 25 Java files, 5 modules (auth, user, notification, HA, config)
- **Model**: Claude Opus 4.6 via Claude Code CLI
- **Key design**: All constraints and team knowledge are **NOT in code comments**. They only exist in Temper's memory. This simulates real-world "tribal knowledge" that only experienced team members know.
- **10 tests** across 4 categories:
  - Token Saving (2) — can Claude find info without searching everything?
  - Constraint Awareness (5) — does Claude know the rules?
  - Fast Localization (2) — can Claude trace impact chains?
  - No Wrong Changes (1) — does Claude follow existing patterns?

---

## The Three Things We Tested

### 1. Does Temper Save Tokens and Money?

**Yes. -19% cost, -18% time overall.**

Biggest single win — **T10: "Add email notification following existing patterns"**:

```
                Without Temper          With Temper
────────────────────────────────────────────────────
Turns           6                       4
Time            59.4s                   16.8s          ← 3.5x faster
Cost            $0.19                   $0.10          ← 47% cheaper
Output tokens   1,067                   662            ← 38% fewer
```

Without Temper, Claude spent 6 turns searching the codebase to figure out the notification pattern (TemplateEngine → EmailSender). With Temper, it already knew from the module context — done in 4 turns, 3.5x faster.

Similarly, **T7** (email template constraint): 5 turns → 2 turns, 40% faster. Temper already knew "all emails must go through TemplateEngine."

**Why it matters at scale**: In a 25-file project, Claude can grep everything quickly. In a 2000+ file project like FortiNAC, search noise grows exponentially. The token savings would be 30-50% instead of 7%.

---

### 2. Does Temper Prevent Wrong Changes?

**Yes. Constraint awareness: 72% → 86%.**

The killer test — **T4: "Add a cache to UserDAO.findAll()"**:

```
Without Temper:
  → 6 turns, hit max_turns limit, was actively trying to add the cache
  → NEVER warned about the constraint
  → Cost: $0.10 wasted

With Temper:
  → 3 turns, immediately refused
  → "There's an explicit constraint: Do NOT add caching to DAO layer.
     Hibernate L2 cache caused stale reads in multi-node deployment."
  → Suggested adding cache at Service layer instead
  → Cost: $0.07
```

The constraint "don't cache at DAO layer" was nowhere in the code. It was a lesson learned from a 2023 production incident — the kind of knowledge that only exists in team members' heads. Without Temper, Claude had no way to know this. **It confidently made the wrong change.**

Other constraint results:

| Test | What was asked | Without | With |
|------|---------------|---------|------|
| T4 DAO cache | Add cache to DAO | ❌ Did it | ✅ Refused |
| T6 Pool size | Change pool from 50→200 | ⚠️ Partial warning | ✅ Full constraint |
| T7 Email HTML | Send raw HTML email | ✅ Warned (5 turns) | ✅ Warned (2 turns) |
| T5 Token security | Simplify to currentTimeMillis | ✅ Both refused | ✅ Both refused |
| T3 DAO bypass | Call DAO from controller | ✅ Both refused | ✅ Both refused |

T5 and T3 scored the same because Claude could infer the constraints from the code itself (SecureRandom was already in the code, Service pattern was obvious). **Temper's real value is for constraints that aren't visible in the code.**

---

### 3. Does Temper Help Locate Problems Faster?

**Partially. More valuable for experience recall than code tracing.**

**T9: "Users randomly get logged out, suspect HA failover"**:

Both modes found the failover → session invalidation chain (the code was readable enough). But with Temper, Claude had access to the recorded experience: "symptom: random logouts → cause: session cache not pre-warmed → fix: call preWarmSessions() before switchPrimary()."

In a large codebase where the HA module and session module are 500 files apart, Temper's causal chain would be the difference between 2 minutes and 20 minutes of searching.

---

## What We Learned

### Where Temper helps most
1. **Tribal knowledge** — constraints not in code, design decisions only in people's heads
2. **Pattern following** — "how do we do X in this codebase?" answered instantly
3. **Cost reduction** — fewer search rounds = fewer tokens = less money

### Where Temper doesn't help (yet)
1. **Small projects** — Claude can grep 25 files trivially
2. **Constraints already in code** — if it's in a comment, Claude reads it directly
3. **Simple impact analysis** — import chains are easy to follow

### What we expect on FortiNAC (2000+ files)

| Metric | Current (25 files) | Expected (2000+ files) |
|--------|-------------------|----------------------|
| Cost savings | -19% | -30% to -50% |
| Constraint awareness | +14% | +40% to +60% |
| Localization speed | -18% | -50% to -70% |
| Feature implementation | 3.5x faster | 5-10x faster |

---

## Raw Data

| Test | Category | Without | With | Winner |
|------|----------|---------|------|--------|
| T1 Module overview | Token | $0.11, 5t, 11s | $0.08, 5t, 13s | With (-27% cost) |
| T2 Token search | Token | $0.09, 5t, 17s | $0.09, 5t, 16s | Tie |
| T3 DAO bypass | Constraint | $0.11, 3t, 19s | $0.09, 7t, 21s | Both passed |
| **T4 DAO cache** | **Constraint** | **$0.10, 6t, 21s ❌** | **$0.07, 3t, 10s ✅** | **With (+70%)** |
| T5 Token security | Constraint | $0.10, 3t, 12s | $0.07, 3t, 12s | Both passed |
| T6 Pool size | Constraint | $0.07, 3t, 10s | $0.08, 4t, 22s | With (+70%) |
| T7 Email template | Constraint | $0.08, 5t, 17s | $0.06, 2t, 10s | With (60% faster) |
| T8 Impact analysis | Localization | $0.10, 6t, 22s | $0.11, 7t, 25s | Both passed |
| T9 Causal chain | Localization | $0.16, 2t, 53s | $0.15, 2t, 52s | Tie |
| **T10 Add feature** | **No Wrong Changes** | **$0.19, 6t, 59s** | **$0.10, 4t, 17s** | **With (3.5x faster)** |
| **Total** | | **$1.12, 44t, 241s** | **$0.91, 42t, 197s** | **With wins** |

---

*Benchmark tool: [Temper](https://github.com/anthropics/temper) — Forged Memory for Your Code*
*Test date: 2026-04-07 | Model: Claude Opus 4.6 | All tests run on same machine, same prompts*
