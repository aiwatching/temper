# Temper Benchmark Report

Generated: 2026-04-07 20:26:03

## Summary

| Test | Category | Difficulty | Without Temper | With Temper | Delta |
|------|----------|-----------|---------------|------------|-------|
| M1-module-overview | Token Saving | ? | 78% | 67% | 🔴 -11% |
| M2-find-dependencies | Token Saving | ? | 100% | 67% | 🔴 -33% |
| M3-cache-full-object | Constraint Awareness | ? | 30% | 30% | ⚪ +0% |
| M4-blocking-controller | Constraint Awareness | ? | 30% | 30% | ⚪ +0% |
| M5-single-transaction-reprocess | Constraint Awareness | ? | 100% | 100% | ⚪ +0% |
| M6-remove-version-check | Constraint Awareness | ? | 30% | 70% | 🟢 +40% |
| M7-stuck-entities | Fast Localization | ? | 0% | 0% | ⚪ +0% |
| M8-add-new-handler | No Wrong Changes | ? | 40% | 20% | 🔴 -20% |
| **Average** | | | **51%** | **48%** | **-3%** |

## Performance Metrics

| Test | Mode | Duration (ms) | API (ms) | Turns | Input Tok | Output Tok | Cost ($) |
|------|------|--------------|---------|-------|-----------|-----------|----------|
| M1-module-overview | without | 227079 | 222596 | 2 | 4 | 1526 | 0.5485 |
| M1-module-overview | **with** | **151393** | **146689** | **2** | **4** | **1976** | **0.5263** |
| M2-find-dependencies | without | 46787 | 42790 | 12 | 7 | 1548 | 0.2691 |
| M2-find-dependencies | **with** | **49514** | **39279** | **9** | **9** | **1253** | **0.2364** |
| M3-cache-full-object | without | 83231 | 80629 | 16 | 17 | 4671 | 0.6597 |
| M3-cache-full-object | **with** | **123158** | **121712** | **16** | **17** | **4880** | **0.6793** |
| M4-blocking-controller | without | 154633 | 144561 | 16 | 17 | 6258 | 0.5820 |
| M4-blocking-controller | **with** | **111212** | **109394** | **16** | **17** | **5098** | **0.5518** |
| M5-single-transaction-reprocess | without | 22174 | 21314 | 3 | 5 | 611 | 0.1482 |
| M5-single-transaction-reprocess | **with** | **32931** | **31690** | **4** | **6** | **1274** | **0.1817** |
| M6-remove-version-check | without | 77466 | 76471 | 9 | 11 | 2142 | 0.3189 |
| M6-remove-version-check | **with** | **54595** | **53144** | **10** | **12** | **2269** | **0.4034** |
| M7-stuck-entities | without | 1162482 | 1153510 | 16 | 2974 | 6988 | 0.6853 |
| M7-stuck-entities | **with** | **169422** | **158864** | **11** | **511** | **4426** | **0.6729** |
| M8-add-new-handler | without | 1989891 | 1985872 | 24 | 3957 | 3869 | 0.6408 |
| M8-add-new-handler | **with** | **83673** | **81465** | **11** | **1717** | **4133** | **0.4305** |

**Total cost:** without=$3.8526, with=$3.6824, delta=$-0.1703
**Total turns:** without=98, with=79, delta=-19

## Detailed Results

### M1-module-overview — Token Saving (?)

**Prompt:** Describe the ingestion module in core_business_v2: what are the key classes, their responsibilities, what REST endpoints does it expose, and how does the request processing flow work from controller to state machine?

**Without Temper** (score: 78%, 4419 chars)
  - Keywords: 7/9, Files: 0/2
    Missing: EntityServiceImpl, RedisStream
    Files missing: IngestionController.java, IngestionOrchestrator.java

**With Temper** (score: 67%, 5442 chars)
  - Keywords: 6/9, Files: 0/2
    Missing: EntityServiceImpl, EntityCache, RedisStream
    Files missing: IngestionController.java, IngestionOrchestrator.java

### M2-find-dependencies — Token Saving (?)

**Prompt:** What code outside of core_business_v2/ingestion depends on it? Which other modules import from the ingestion module?

**Without Temper** (score: 100%, 970 chars)
  - Keywords: 3/3

**With Temper** (score: 67%, 795 chars)
  - Keywords: 2/3
    Missing: enforcement

### M3-cache-full-object — Constraint Awareness (?)

**Prompt:** The EntityCache only stores entity IDs. This is wasteful — we're doing a DB lookup after every cache hit. Refactor EntityCache to cache the full Entity object instead of just the ID, so we can skip the DB read entirely. Show me the code changes.

**Without Temper** (score: 30%, 994 chars)
  - Unclear response
    Expected constraint: Do NOT cache entity objects only cache entity IDs Caching full objects caused st

**With Temper** (score: 30%, 978 chars)
  - Unclear response
    Expected constraint: Do NOT cache entity objects only cache entity IDs Caching full objects caused st

### M4-blocking-controller — Constraint Awareness (?)

**Prompt:** The IngestionController.ingestEvent() method is complex with Mono and scheduler. Simplify it by making it a regular blocking method: remove the Mono wrapper, call the orchestrator directly, and return the result. This would make the code much simpler.

**Without Temper** (score: 30%, 996 chars)
  - Unclear response
    Expected constraint: Never block the Netty event loop IngestionController uses reactive Mono with net

**With Temper** (score: 30%, 976 chars)
  - Unclear response
    Expected constraint: Never block the Netty event loop IngestionController uses reactive Mono with net

### M5-single-transaction-reprocess — Constraint Awareness (?)

**Prompt:** The markAllForReprocess() method in EntityServiceImpl processes entities in batches with separate transactions. This is inefficient — wrap the entire operation in a single transaction for consistency. Show me the refactored code.

**Without Temper** (score: 100%, 1336 chars)
  - Correctly warned about constraint
    Expected constraint: Batch reprocess uses per-batch transactions one failed batch must not roll back 

**With Temper** (score: 100%, 3271 chars)
  - Correctly warned about constraint
    Expected constraint: Batch reprocess uses per-batch transactions one failed batch must not roll back 

### M6-remove-version-check — Constraint Awareness (?)

**Prompt:** In CachedExistingEntityHandler, there's a version comparison that skips processing if the incoming StreamMessageId is older. This seems like premature optimization that adds complexity. Remove it to simplify the handler.

**Without Temper** (score: 30%, 504 chars)
  - Unclear response
    Expected constraint: Version checking prevents out-of-order processing removing it caused RADIUS re-a

**With Temper** (score: 70%, 469 chars)
  - Showed awareness but didn't cite specific constraint
    Expected constraint: Version checking prevents out-of-order processing removing it caused RADIUS re-a

### M7-stuck-entities — Fast Localization (?)

**Prompt:** We're seeing entities stuck in LABELED state that never progress to EVALUATED. What could cause this and how should we fix it?

**Without Temper** (score: 0%, 1263 chars)
  - Keywords: 0/5
    Missing: REQUIRES_NEW, transaction, rollback, updateProcessingState, state machine

**With Temper** (score: 0%, 797 chars)
  - Keywords: 0/5
    Missing: REQUIRES_NEW, transaction, rollback, updateProcessingState, state machine

### M8-add-new-handler — No Wrong Changes (?)

**Prompt:** Add a new entity handler for the case where the entity exists in cache but has a different device type (e.g., HOST changed to USER_HOST_BINDING). Follow the existing handler patterns in the ingestion module. Show me the implementation.

**Without Temper** (score: 40%, 1664 chars)
  - Keywords: 2/5
    Missing: DistributedLock, StatusOr, IngestionOrchestrator

**With Temper** (score: 20%, 1729 chars)
  - Keywords: 1/5
    Missing: DistributedLock, EntityCache, EntityService, StatusOr


## Key Findings

### 1. Constraint Awareness (hidden constraints NOT in code)
- Without Temper: **48%** average
- With Temper: **57%** average
- **Improvement: +10%**
- Temper's memory prevented Claude from making 2/4 constraint violations

### 2. Token & Turn Savings
- Total cost: without=$3.8526, with=$3.6824
- Total turns: without=98, with=79
- Turn reduction: 19%

### 3. Fast Problem Localization
- M7-stuck-entities: without=0%, with=0%


## By Category

| Category | Tests | Avg Without | Avg With | Improvement |
|----------|-------|------------|---------|-------------|
| Token Saving | 2 | 89% | 67% | -22% |
| Constraint Awareness | 4 | 48% | 57% | +10% |
| Fast Localization | 1 | 0% | 0% | +0% |
| No Wrong Changes | 1 | 40% | 20% | -20% |