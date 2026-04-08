# Temper Benchmark Report

Generated: 2026-04-07 18:37:27

## Summary

| Test | Category | Difficulty | Without Temper | With Temper | Delta |
|------|----------|-----------|---------------|------------|-------|
| M1-module-overview | Token Saving | ? | 89% | 78% | 🔴 -11% |
| M2-find-dependencies | Token Saving | ? | 33% | 100% | 🟢 +67% |
| M3-cache-full-object | Constraint Awareness | ? | 30% | 30% | ⚪ +0% |
| M4-blocking-controller | Constraint Awareness | ? | 100% | 30% | 🔴 -70% |
| M5-single-transaction-reprocess | Constraint Awareness | ? | 100% | 100% | ⚪ +0% |
| M6-remove-version-check | Constraint Awareness | ? | 70% | 70% | ⚪ +0% |
| M7-stuck-entities | Fast Localization | ? | 60% | 0% | 🔴 -60% |
| M8-add-new-handler | No Wrong Changes | ? | 0% | 20% | 🟢 +20% |
| **Average** | | | **60%** | **53%** | **-7%** |

## Performance Metrics

| Test | Mode | Duration (ms) | API (ms) | Turns | Input Tok | Output Tok | Cost ($) |
|------|------|--------------|---------|-------|-----------|-----------|----------|
| M1-module-overview | without | 150229 | 145604 | 2 | 4 | 1683 | 0.5152 |
| M1-module-overview | **with** | **238358** | **233852** | **2** | **4** | **1597** | **0.6332** |
| M2-find-dependencies | without | 29039 | 26360 | 6 | 7 | 883 | 0.2157 |
| M2-find-dependencies | **with** | **43148** | **41564** | **10** | **7** | **1434** | **0.3146** |
| M3-cache-full-object | without | 135220 | 134011 | 16 | 17 | 7659 | 0.7680 |
| M3-cache-full-object | **with** | **114046** | **113449** | **16** | **17** | **4700** | **0.6508** |
| M4-blocking-controller | without | 132443 | 129859 | 15 | 16 | 8628 | 0.6581 |
| M4-blocking-controller | **with** | **113186** | **112149** | **13** | **14** | **6440** | **0.5414** |
| M5-single-transaction-reprocess | without | 33278 | 32456 | 4 | 6 | 1330 | 0.1823 |
| M5-single-transaction-reprocess | **with** | **28069** | **26352** | **3** | **5** | **629** | **0.1504** |
| M6-remove-version-check | without | 58616 | 57620 | 9 | 11 | 2072 | 0.3213 |
| M6-remove-version-check | **with** | **54505** | **53625** | **8** | **10** | **1928** | **0.3026** |
| M7-stuck-entities | without | 216049 | 193779 | 15 | 12 | 4074 | 0.8035 |
| M7-stuck-entities | **with** | **191313** | **185308** | **11** | **9** | **7793** | **0.7339** |
| M8-add-new-handler | without | 70249 | 67087 | 16 | 1717 | 3365 | 0.4417 |
| M8-add-new-handler | **with** | **78943** | **77842** | **13** | **1715** | **3162** | **0.4198** |

**Total cost:** without=$3.9060, with=$3.7467, delta=$-0.1592
**Total turns:** without=83, with=76, delta=-7

## Detailed Results

### M1-module-overview — Token Saving (?)

**Prompt:** Describe the ingestion module in core_business_v2: what are the key classes, their responsibilities, what REST endpoints does it expose, and how does the request processing flow work from controller to state machine?

**Without Temper** (score: 89%, 4691 chars)
  - Keywords: 8/9, Files: 0/2
    Missing: RedisStream
    Files missing: IngestionController.java, IngestionOrchestrator.java

**With Temper** (score: 78%, 4201 chars)
  - Keywords: 7/9, Files: 0/2
    Missing: EntityServiceImpl, RedisStream
    Files missing: IngestionController.java, IngestionOrchestrator.java

### M2-find-dependencies — Token Saving (?)

**Prompt:** What code outside of core_business_v2/ingestion depends on it? Which other modules import from the ingestion module?

**Without Temper** (score: 33%, 770 chars)
  - Keywords: 1/3
    Missing: common, enforcement

**With Temper** (score: 100%, 1075 chars)
  - Keywords: 3/3

### M3-cache-full-object — Constraint Awareness (?)

**Prompt:** The EntityCache only stores entity IDs. This is wasteful — we're doing a DB lookup after every cache hit. Refactor EntityCache to cache the full Entity object instead of just the ID, so we can skip the DB read entirely. Show me the code changes.

**Without Temper** (score: 30%, 996 chars)
  - Unclear response
    Expected constraint: Do NOT cache entity objects only cache entity IDs Caching full objects caused st

**With Temper** (score: 30%, 978 chars)
  - Unclear response
    Expected constraint: Do NOT cache entity objects only cache entity IDs Caching full objects caused st

### M4-blocking-controller — Constraint Awareness (?)

**Prompt:** The IngestionController.ingestEvent() method is complex with Mono and scheduler. Simplify it by making it a regular blocking method: remove the Mono wrapper, call the orchestrator directly, and return the result. This would make the code much simpler.

**Without Temper** (score: 100%, 905 chars)
  - Correctly warned about constraint
    Expected constraint: Never block the Netty event loop IngestionController uses reactive Mono with net

**With Temper** (score: 30%, 774 chars)
  - Unclear response
    Expected constraint: Never block the Netty event loop IngestionController uses reactive Mono with net

### M5-single-transaction-reprocess — Constraint Awareness (?)

**Prompt:** The markAllForReprocess() method in EntityServiceImpl processes entities in batches with separate transactions. This is inefficient — wrap the entire operation in a single transaction for consistency. Show me the refactored code.

**Without Temper** (score: 100%, 2914 chars)
  - Correctly warned about constraint
    Expected constraint: Batch reprocess uses per-batch transactions one failed batch must not roll back 

**With Temper** (score: 100%, 1344 chars)
  - Correctly warned about constraint
    Expected constraint: Batch reprocess uses per-batch transactions one failed batch must not roll back 

### M6-remove-version-check — Constraint Awareness (?)

**Prompt:** In CachedExistingEntityHandler, there's a version comparison that skips processing if the incoming StreamMessageId is older. This seems like premature optimization that adds complexity. Remove it to simplify the handler.

**Without Temper** (score: 70%, 415 chars)
  - Showed awareness but didn't cite specific constraint
    Expected constraint: Version checking prevents out-of-order processing removing it caused RADIUS re-a

**With Temper** (score: 70%, 483 chars)
  - Showed awareness but didn't cite specific constraint
    Expected constraint: Version checking prevents out-of-order processing removing it caused RADIUS re-a

### M7-stuck-entities — Fast Localization (?)

**Prompt:** We're seeing entities stuck in LABELED state that never progress to EVALUATED. What could cause this and how should we fix it?

**Without Temper** (score: 60%, 4349 chars)
  - Keywords: 3/5
    Missing: REQUIRES_NEW, updateProcessingState

**With Temper** (score: 0%, 1350 chars)
  - Keywords: 0/5
    Missing: REQUIRES_NEW, transaction, rollback, updateProcessingState, state machine

### M8-add-new-handler — No Wrong Changes (?)

**Prompt:** Add a new entity handler for the case where the entity exists in cache but has a different device type (e.g., HOST changed to USER_HOST_BINDING). Follow the existing handler patterns in the ingestion module. Show me the implementation.

**Without Temper** (score: 0%, 1306 chars)
  - Keywords: 0/5
    Missing: DistributedLock, EntityCache, EntityService, StatusOr, IngestionOrchestrator

**With Temper** (score: 20%, 1713 chars)
  - Keywords: 1/5
    Missing: DistributedLock, EntityCache, EntityService, StatusOr


## Key Findings

### 1. Constraint Awareness (hidden constraints NOT in code)
- Without Temper: **75%** average
- With Temper: **57%** average
- **Improvement: -18%**

### 2. Token & Turn Savings
- Total cost: without=$3.9060, with=$3.7467
- Total turns: without=83, with=76
- Turn reduction: 8%

### 3. Fast Problem Localization
- M7-stuck-entities: without=60%, with=0%


## By Category

| Category | Tests | Avg Without | Avg With | Improvement |
|----------|-------|------------|---------|-------------|
| Token Saving | 2 | 61% | 89% | +28% |
| Constraint Awareness | 4 | 75% | 57% | -18% |
| Fast Localization | 1 | 60% | 0% | -60% |
| No Wrong Changes | 1 | 0% | 20% | +20% |