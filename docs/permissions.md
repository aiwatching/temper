# Permissions

The full picture of who can read and write what — including the
deliberately-restricted bits MVP ships.

## TL;DR

A **namespace** is a flat string that scopes an Episode. Two MVP rules
matter:

1. You write to your own `user:<id>`. That's `user:me` if you can't be
   bothered to fetch it. Everything you write is private to you.
2. You read your own stuff, anything in `public`, and any
   group/org you belong to (the latter currently zero since Phase 1.3
   isn't wired).

Everything else is denied. **Default deny** — anything not on the
allow-list is rejected.

---

## Namespace shapes (PRD §4.2)

| Form | Example |
|---|---|
| `user:<uuid>` | `user:2a4068c5-6377-4b42-ac85-60e753d473dd` |
| `user:me` (alias) | resolved to `user:<caller-uuid>` |
| `group:<slug>` | `group:fortinac-team` |
| `org:<slug>` | `org:fortinet` |
| `public` | `public` |

In Graphiti's internal `group_id`, `:` is encoded as `__` and `-` is
encoded as `_` (FalkorDB + RediSearch reject both in raw form). You
never see that translation; the API surface uses the raw form.

---

## Write matrix

| Caller \ Target            | `user:self` | `user:other` | `group:my-X` | `group:other-X` | `org:my-Y` | `org:other-Y` | `public` |
|---|---|---|---|---|---|---|---|
| user, no org               | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| org member                 | ✓ | ✗ | (if in group) | ✗ | ✗ | ✗ | ✗ |
| group member               | ✓ | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ |
| org admin (`is_org_admin`) | ✓ | ✗ | ✓ (any group in org) | ✗ | ✓ | ✗ | ✗ |
| super_admin                | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

Notes:

- `is_org_admin` is a per-user bool. Because the schema constrains a
  user to a single org (`User.org_id`), the role is implicitly scoped to
  that org — switching orgs forfeits it.
- `org admin` can manage all groups inside their org (rename, delete,
  membership changes), even ones they're not personally a member of.

## Read matrix

| Caller \ Target | `user:self` | `user:other` | `group:my-X` | `group:other-X` | `org:my-Y` | `org:other-Y` | `public` |
|---|---|---|---|---|---|---|---|
| anonymous       | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| authenticated   | ✓ | ✗ | (if member) | ✗ | (if in org) | ✗ | ✓ |
| super_admin     | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

---

## Org / group management

- Orgs are created only by super_admin (`POST /v1/orgs`). Slugs are
  global — squatting risk if we ever opened this up.
- Org admins (or super_admin) add members via
  `POST /v1/orgs/{slug}/members`. Users belong to at most one org.
- Any org member can create groups in their own org
  (`POST /v1/groups`). The creator is auto-added as that group's admin.
- Group admins manage their group's members
  (`POST/PATCH/DELETE /v1/groups/{slug}/members`). Org admins can do
  the same across every group in their org.
- Removing a user from an org also drops their group memberships in
  groups belonging to that org — keeps read access consistent with org
  membership.

## What's still out of scope

- **`public` writes** are super_admin-only by design. Promote yourself
  via `BOOTSTRAP_SUPER_ADMIN_EMAIL` if you legitimately need to put
  shared knowledge there.
- **Multi-org-per-user** would need a `UserOrgMembership` table; we
  punted because nothing in v0.5 needs it.
- **Per-fact / per-entity ACLs** — explicitly out of scope. Namespace
  is the only privacy boundary.

---

## How permission denials surface

A blocked write returns **403** with a hint that includes your own
namespace:

```json
{"detail": "User 'you@example.com' cannot write to namespace 'user:other-id'. Your own namespace is 'user:2a4068c5-…' (or just leave it blank / use 'user:me')."}
```

A blocked read on a single Episode returns **404** instead of 403 —
this is deliberate. 403 would leak the existence of an Episode the
caller shouldn't know about. Search and list silently filter out
unreadable namespaces.

---

## Bootstrap super_admin

Set `BOOTSTRAP_SUPER_ADMIN_EMAIL=you@example.com` in `.env`. Two
promotion paths run automatically:

- If that email registers fresh, they're created as super_admin.
- If they're already in the DB but plain, the next app boot promotes
  them.

Both are idempotent. Drop the env var when you don't want this any
more — existing super_admins keep their flag (no demotion happens).

---

## Where this is checked

| File | Role |
|---|---|
| `core/namespaces.py` | parse + resolve + can_read + can_write + readable_namespaces_for |
| `core/memory.py` | call into namespaces from every memory op |
| `api/deps.py` | resolve `CurrentUser` from API key or JWT |
| `core/bootstrap.py` | super_admin promotion |

The matrix lives in **one** file (`namespaces.py`). When Phase 1.4
extends it, that's the only place the rules change.
