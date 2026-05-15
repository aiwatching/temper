---
name: mantis-bug-format
description: |
  Conventions for writing a Mantis bug report. Load when the user is
  drafting a new bug, triaging an open one, or asking how to phrase a
  reproduction step. Covers required fields, severity rubric, and a
  reproducer template.
---

# Mantis bug — house style

When the user is creating or rewriting a Mantis ticket, follow the
conventions below. This skill is example/scaffold content; the real
team-curated version will ship as `@fortinet/smith-skills`.

## Required fields

- **Summary** — single line, action-oriented, starts with the affected
  component in brackets. Example:
  `[fortinac/policy-engine] Endpoint health check times out after 5s on slow LDAP`
- **Severity** — one of `S1` / `S2` / `S3` / `S4` (rubric below).
- **Affected version** — full build string (`7.4.2 GA build 1234`),
  not "latest".
- **Reproducer** — six lines, see template.

## Severity rubric

| Tier | Trigger |
|---|---|
| S1 | Production outage, data loss, security exposure with active exploit. Page oncall. |
| S2 | Functional regression blocking a release-gate test. |
| S3 | Functional bug with workaround. Default for unclassified. |
| S4 | Polish / cosmetic / docs. |

## Reproducer template

```
Environment:
  - Build:
  - Topology:
  - Auth method:

Steps:
  1.
  2.
  3.

Observed:
Expected:
```

## What to AVOID

- Don't write "doesn't work" in the summary. Be specific about WHAT failed.
- Don't paste full logs in the body — attach as a file or use a Mantis
  log gist link.
- Don't mark severity S1 unless you've paged oncall. Severity is for
  on-call routing, not "I think this is important".
