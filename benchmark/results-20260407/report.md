# Temper Benchmark Report

Generated: 2026-04-07 14:26:38

## Summary

| Test | Category | Difficulty | Without Temper | With Temper | Delta |
|------|----------|-----------|---------------|------------|-------|
| T1-token-saving-module | Token Saving | easy | 100% | 100% | ⚪ +0% |
| T2-token-saving-search | Token Saving | medium | 100% | 100% | ⚪ +0% |
| T3-constraint-dao-bypass | Constraint Awareness | hard | 100% | 100% | ⚪ +0% |
| T4-constraint-dao-cache | Constraint Awareness | hard | 30% | 100% | 🟢 +70% |
| T5-constraint-token-security | Constraint Awareness | hard | 100% | 30% | 🔴 -70% |
| T6-constraint-pool-size | Constraint Awareness | hard | 30% | 100% | 🟢 +70% |
| T7-constraint-email-template | Constraint Awareness | hard | 100% | 100% | ⚪ +0% |
| T8-localization-impact | Fast Localization | medium | 100% | 100% | ⚪ +0% |
| T9-localization-causal | Fast Localization | hard | 80% | 80% | ⚪ +0% |
| T10-no-wrong-changes | No Wrong Changes | hard | 33% | 50% | 🟢 +17% |
| **Average** | | | **77%** | **86%** | **+9%** |

## Performance Metrics

| Test | Mode | Duration (ms) | API (ms) | Turns | Input Tok | Output Tok | Cost ($) |
|------|------|--------------|---------|-------|-----------|-----------|----------|
| T1-token-saving-module | without | 14315 | - | - | - | - | 0.0000 |
| T1-token-saving-module | **with** | **16451** | **-** | **-** | **-** | **-** | **0.0000** |
| T2-token-saving-search | without | 19190 | - | - | - | - | 0.0000 |
| T2-token-saving-search | **with** | **18079** | **-** | **-** | **-** | **-** | **0.0000** |
| T3-constraint-dao-bypass | without | 23117 | - | - | - | - | 0.0000 |
| T3-constraint-dao-bypass | **with** | **23079** | **-** | **-** | **-** | **-** | **0.0000** |
| T4-constraint-dao-cache | without | 20457 | 20376 | 6 | 7 | 690 | 0.1021 |
| T4-constraint-dao-cache | **with** | **12521** | **-** | **-** | **-** | **-** | **0.0000** |
| T5-constraint-token-security | without | 14535 | - | - | - | - | 0.0000 |
| T5-constraint-token-security | **with** | **14274** | **-** | **-** | **-** | **-** | **0.0000** |
| T6-constraint-pool-size | without | 13211 | - | - | - | - | 0.0000 |
| T6-constraint-pool-size | **with** | **24849** | **-** | **-** | **-** | **-** | **0.0000** |
| T7-constraint-email-template | without | 20339 | - | - | - | - | 0.0000 |
| T7-constraint-email-template | **with** | **13192** | **-** | **-** | **-** | **-** | **0.0000** |
| T8-localization-impact | without | 25772 | - | - | - | - | 0.0000 |
| T8-localization-impact | **with** | **27441** | **-** | **-** | **-** | **-** | **0.0000** |
| T9-localization-causal | without | 56290 | - | - | - | - | 0.0000 |
| T9-localization-causal | **with** | **55010** | **-** | **-** | **-** | **-** | **0.0000** |
| T10-no-wrong-changes | without | 63247 | - | - | - | - | 0.0000 |
| T10-no-wrong-changes | **with** | **19370** | **-** | **-** | **-** | **-** | **0.0000** |

**Total cost:** without=$0.1021, with=$0.0000, delta=$-0.1021
**Total turns:** without=6, with=0, delta=-6

## Detailed Results

### T1-token-saving-module — Token Saving (easy)

**Prompt:** List all REST endpoints in the user module with their HTTP methods, URL paths, and handler methods. Also list all the module's dependencies on other modules.

**Without Temper** (score: 100%, 1611 chars)
  - Keywords: 8/8, Files: 0/2
    Files missing: UserController.java, UserService.java

**With Temper** (score: 100%, 1738 chars)
  - Keywords: 8/8, Files: 0/2
    Files missing: UserController.java, UserService.java

### T2-token-saving-search — Token Saving (medium)

**Prompt:** I need to understand all token-related code in this project. Where are tokens generated, validated, and refreshed? Give me the exact file paths and method names.

**Without Temper** (score: 100%, 2553 chars)
  - Keywords: 6/6, Files: 2/2

**With Temper** (score: 100%, 2235 chars)
  - Keywords: 6/6, Files: 2/2

### T3-constraint-dao-bypass — Constraint Awareness (hard)

**Prompt:** UserController.getUser() is slow. Optimize it by calling UserDAO.findById() directly instead of going through UserService.findById(). Show me the code.

**Without Temper** (score: 100%, 2191 chars)
  - Correctly warned about constraint
    Expected constraint: All user operations MUST go through UserService Direct DAO access bypasses valid

**With Temper** (score: 100%, 2484 chars)
  - Correctly warned about constraint
    Expected constraint: All user operations MUST go through UserService Direct DAO access bypasses valid

### T4-constraint-dao-cache — Constraint Awareness (hard)

**Prompt:** Add an in-memory cache (ConcurrentHashMap) to UserDAO.findAll() to avoid hitting the database every time. Show me the implementation.

**Without Temper** (score: 30%, 964 chars)
  - Unclear response
    Expected constraint: Do NOT add caching to DAO layer Hibernate L2 cache caused stale reads in multi-n

**With Temper** (score: 100%, 1308 chars)
  - Correctly warned about constraint
    Expected constraint: Do NOT add caching to DAO layer Hibernate L2 cache caused stale reads in multi-n

### T5-constraint-token-security — Constraint Awareness (hard)

**Prompt:** The TokenManager.generate() method is complex. Simplify it to just return username + '-' + System.currentTimeMillis(). This is simpler and faster.

**Without Temper** (score: 100%, 1682 chars)
  - Correctly warned about constraint
    Expected constraint: Token generation MUST use SecureRandom Vulnerability CVE was caused by predictab

**With Temper** (score: 30%, 1576 chars)
  - Unclear response
    Expected constraint: Token generation MUST use SecureRandom Vulnerability CVE was caused by predictab

### T6-constraint-pool-size — Constraint Awareness (hard)

**Prompt:** The database is slow under load. Increase getDbPoolSize() in AppConfig from 50 to 200. Show me the change.

**Without Temper** (score: 30%, 1369 chars)
  - Unclear response
    Expected constraint: Database pool size must match HA node count pool_size equals max_connections div

**With Temper** (score: 100%, 1166 chars)
  - Correctly warned about constraint
    Expected constraint: Database pool size must match HA node count pool_size equals max_connections div

### T7-constraint-email-template — Constraint Awareness (hard)

**Prompt:** Add a sendAlert() method to NotificationService that sends a simple HTML email: '<h1>Alert</h1><p>' + message + '</p>'. Call EmailSender.send() directly for simplicity.

**Without Temper** (score: 100%, 2085 chars)
  - Correctly warned about constraint
    Expected constraint: All emails must go through TemplateEngine Raw HTML emails blocked by security po

**With Temper** (score: 100%, 1610 chars)
  - Correctly warned about constraint
    Expected constraint: All emails must go through TemplateEngine Raw HTML emails blocked by security po

### T8-localization-impact — Fast Localization (medium)

**Prompt:** I want to change the return type of UserService.findById() from User to Optional<User>. List every file that would need to be updated and explain why.

**Without Temper** (score: 100%, 2443 chars)
  - Files identified: 2/2

**With Temper** (score: 100%, 2940 chars)
  - Files identified: 2/2

### T9-localization-causal — Fast Localization (hard)

**Prompt:** Our users report being randomly logged out. We suspect it's related to our HA failover. What is the chain of events, and what was the fix?

**Without Temper** (score: 80%, 3164 chars)
  - Keywords: 4/5, Files: 2/2
    Missing: pre-warm

**With Temper** (score: 80%, 2871 chars)
  - Keywords: 4/5, Files: 0/2
    Missing: switchPrimary
    Files missing: HAManager.java, SessionStore.java

### T10-no-wrong-changes — No Wrong Changes (hard)

**Prompt:** Add a 'user updated' email notification when UserService.update() is called. Follow existing patterns in the codebase.

**Without Temper** (score: 33%, 1730 chars)
  - Keywords: 1/3
    Missing: TemplateEngine, templateEngine.render

**With Temper** (score: 50%, 1729 chars)
  - Keywords: 3/3, Anti-patterns found: ['EmailSender.send']


## Key Findings

### 1. Constraint Awareness (hidden constraints NOT in code)
- Without Temper: **72%** average
- With Temper: **86%** average
- **Improvement: +14%**
- Temper's memory prevented Claude from making 4/5 constraint violations

### 2. Token & Turn Savings
- Total cost: without=$0.1021, with=$0.0000
- Total turns: without=6, with=0
- Turn reduction: 100%

### 3. Fast Problem Localization
- T8-localization-impact: without=100%, with=100%
- T9-localization-causal: without=80%, with=80%


## By Category

| Category | Tests | Avg Without | Avg With | Improvement |
|----------|-------|------------|---------|-------------|
| Token Saving | 2 | 100% | 100% | +0% |
| Constraint Awareness | 5 | 72% | 86% | +14% |
| Fast Localization | 2 | 90% | 90% | +0% |
| No Wrong Changes | 1 | 33% | 50% | +17% |