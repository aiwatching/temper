# Temper Benchmark Report

Generated: 2026-04-07 16:51:18

## Summary

| Test | Category | Difficulty | Without Temper | With Temper | Delta |
|------|----------|-----------|---------------|------------|-------|
| M1-module-overview | Token Saving | ? | 67% | 78% | 🟢 +11% |
| M2-find-dependencies | Token Saving | ? | 33% | 0% | 🔴 -33% |
| M3-cache-full-object | Constraint Awareness | ? | 30% | 30% | ⚪ +0% |
| M4-blocking-controller | Constraint Awareness | ? | 100% | 30% | 🔴 -70% |
| M5-single-transaction-reprocess | Constraint Awareness | ? | 100% | 100% | ⚪ +0% |
| M6-remove-version-check | Constraint Awareness | ? | 30% | 70% | 🟢 +40% |
| M7-stuck-entities | Fast Localization | ? | 20% | 20% | ⚪ +0% |
| M8-add-new-handler | No Wrong Changes | ? | 0% | 0% | ⚪ +0% |
| **Average** | | | **48%** | **41%** | **-7%** |

## Performance Metrics

| Test | Mode | Duration (ms) | API (ms) | Turns | Input Tok | Output Tok | Cost ($) |
|------|------|--------------|---------|-------|-----------|-----------|----------|
| M1-module-overview | without | 138294 | 125228 | 2 | 4 | 1579 | 0.4727 |
| M1-module-overview | **with** | **259817** | **254188** | **2** | **4** | **1843** | **0.7005** |
| M2-find-dependencies | without | 22734 | 21300 | 6 | 6 | 846 | 0.2144 |
| M2-find-dependencies | **with** | **14158** | **13125** | **4** | **5** | **445** | **0.1557** |
| M3-cache-full-object | without | 60606 | 59821 | 9 | 10 | 2719 | 0.3522 |
| M3-cache-full-object | **with** | **74640** | **71444** | **9** | **10** | **4652** | **0.4928** |
| M4-blocking-controller | without | 38840 | 37304 | 8 | 10 | 1300 | 0.2706 |
| M4-blocking-controller | **with** | **44203** | **43294** | **9** | **10** | **1215** | **0.2701** |
| M5-single-transaction-reprocess | without | 31718 | 30741 | 3 | 5 | 1136 | 0.1614 |
| M5-single-transaction-reprocess | **with** | **23800** | **22885** | **3** | **5** | **1041** | **0.1606** |
| M6-remove-version-check | without | 53838 | 53028 | 9 | 10 | 2051 | 0.3041 |
| M6-remove-version-check | **with** | **9705** | **9509** | **2** | **4** | **291** | **0.1224** |
| M7-stuck-entities | without | 256686 | 246345 | 10 | 9 | 6803 | 0.9110 |
| M7-stuck-entities | **with** | **74861** | **69661** | **8** | **8** | **2056** | **0.3837** |
| M8-add-new-handler | without | 35196 | 35008 | 9 | 1108 | 1886 | 0.3556 |
| M8-add-new-handler | **with** | **63683** | **63139** | **9** | **10** | **3878** | **0.4121** |

**Total cost:** without=$3.0421, with=$2.6980, delta=$-0.3441
**Total turns:** without=56, with=46, delta=-10

## Detailed Results

### M1-module-overview — Token Saving (?)

**Prompt:** Describe the ingestion module in core_business_v2: what are the key classes, their responsibilities, what REST endpoints does it expose, and how does the request processing flow work from controller to state machine?

**Without Temper** (score: 67%, 4092 chars)
  - Keywords: 6/9, Files: 0/2
    Missing: EntityServiceImpl, EntityCache, RedisStream
    Files missing: IngestionController.java, IngestionOrchestrator.java

**With Temper** (score: 78%, 5080 chars)
  - Keywords: 7/9, Files: 0/2
    Missing: EntityServiceImpl, RedisStream
    Files missing: IngestionController.java, IngestionOrchestrator.java

### M2-find-dependencies — Token Saving (?)

**Prompt:** What code outside of core_business_v2/ingestion depends on it? Which other modules import from the ingestion module?

**Without Temper** (score: 33%, 644 chars)
  - Keywords: 1/3
    Missing: common, enforcement

**With Temper** (score: 0%, 354 chars)
  - Keywords: 0/3
    Missing: classification, common, enforcement

### M3-cache-full-object — Constraint Awareness (?)

**Prompt:** The EntityCache only stores entity IDs. This is wasteful — we're doing a DB lookup after every cache hit. Refactor EntityCache to cache the full Entity object instead of just the ID, so we can skip the DB read entirely. Show me the code changes.

**Without Temper** (score: 30%, 994 chars)
  - Unclear response
    Expected constraint: Do NOT cache entity objects only cache entity IDs Caching full objects caused st

**With Temper** (score: 30%, 976 chars)
  - Unclear response
    Expected constraint: Do NOT cache entity objects only cache entity IDs Caching full objects caused st

### M4-blocking-controller — Constraint Awareness (?)

**Prompt:** The IngestionController.ingestEvent() method is complex with Mono and scheduler. Simplify it by making it a regular blocking method: remove the Mono wrapper, call the orchestrator directly, and return the result. This would make the code much simpler.

**Without Temper** (score: 100%, 1121 chars)
  - Correctly warned about constraint
    Expected constraint: Never block the Netty event loop IngestionController uses reactive Mono with net

**With Temper** (score: 30%, 976 chars)
  - Unclear response
    Expected constraint: Never block the Netty event loop IngestionController uses reactive Mono with net

### M5-single-transaction-reprocess — Constraint Awareness (?)

**Prompt:** The markAllForReprocess() method in EntityServiceImpl processes entities in batches with separate transactions. This is inefficient — wrap the entire operation in a single transaction for consistency. Show me the refactored code.

**Without Temper** (score: 100%, 3029 chars)
  - Correctly warned about constraint
    Expected constraint: Batch reprocess uses per-batch transactions one failed batch must not roll back 

**With Temper** (score: 100%, 2728 chars)
  - Correctly warned about constraint
    Expected constraint: Batch reprocess uses per-batch transactions one failed batch must not roll back 

### M6-remove-version-check — Constraint Awareness (?)

**Prompt:** In CachedExistingEntityHandler, there's a version comparison that skips processing if the incoming StreamMessageId is older. This seems like premature optimization that adds complexity. Remove it to simplify the handler.

**Without Temper** (score: 30%, 994 chars)
  - Unclear response
    Expected constraint: Version checking prevents out-of-order processing removing it caused RADIUS re-a

**With Temper** (score: 70%, 613 chars)
  - Showed awareness but didn't cite specific constraint
    Expected constraint: Version checking prevents out-of-order processing removing it caused RADIUS re-a

### M7-stuck-entities — Fast Localization (?)

**Prompt:** We're seeing entities stuck in LABELED state that never progress to EVALUATED. What could cause this and how should we fix it?

**Without Temper** (score: 20%, 1176 chars)
  - Keywords: 1/5
    Missing: REQUIRES_NEW, transaction, rollback, updateProcessingState

**With Temper** (score: 20%, 3940 chars)
  - Keywords: 1/5
    Missing: REQUIRES_NEW, transaction, rollback, updateProcessingState

### M8-add-new-handler — No Wrong Changes (?)

**Prompt:** Add a new entity handler for the case where the entity exists in cache but has a different device type (e.g., HOST changed to USER_HOST_BINDING). Follow the existing handler patterns in the ingestion module. Show me the implementation.

**Without Temper** (score: 0%, 998 chars)
  - Keywords: 0/5
    Missing: DistributedLock, EntityCache, EntityService, StatusOr, IngestionOrchestrator

**With Temper** (score: 0%, 994 chars)
  - Keywords: 0/5
    Missing: DistributedLock, EntityCache, EntityService, StatusOr, IngestionOrchestrator


## Key Findings

### 1. Constraint Awareness (hidden constraints NOT in code)
- Without Temper: **65%** average
- With Temper: **57%** average
- **Improvement: -8%**

### 2. Token & Turn Savings
- Total cost: without=$3.0421, with=$2.6980
- Total turns: without=56, with=46
- Turn reduction: 18%

### 3. Fast Problem Localization
- M7-stuck-entities: without=20%, with=20%


## By Category

| Category | Tests | Avg Without | Avg With | Improvement |
|----------|-------|------------|---------|-------------|
| Token Saving | 2 | 50% | 39% | -11% |
| Constraint Awareness | 4 | 65% | 57% | -8% |
| Fast Localization | 1 | 20% | 20% | +0% |
| No Wrong Changes | 1 | 0% | 0% | +0% |