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

| Caller \ Target | `user:self` | `user:other` | `group:my-X` | `group:other-X` | `org:my-Y` | `public` |
|---|---|---|---|---|---|---|
| user            | ✓ | ✗ | ✓ (Phase 1.3) | ✗ | ✗ | ✗ |
| group admin     | ✓ | ✗ | ✓ | ✗ | ✗ | ✗ |
| org admin       | ✓ | ✗ | ✓ | ✗ | ✓ | ✗ |
| super_admin     | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

## Read matrix

| Caller \ Target | `user:self` | `user:other` | `group:my-X` | `group:other-X` | `org:my-Y` | `public` |
|---|---|---|---|---|---|---|
| anonymous       | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| authenticated   | ✓ | ✗ | ✓ | ✗ | ✓ | ✓ |
| super_admin     | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

---

## MVP gaps and what they mean today

- **`group:<x>` writes** are blocked by Phase 1.3 not being implemented.
  The data model knows about groups + memberships; the API to *put
  someone in* a group doesn't ship yet. So in practice today,
  `group:*` is a "drawer that exists but no key cuts it" — super_admin
  excepted.
- **`org:<x>` writes** require an org_admin role that we model with
  `UserGroupMembership.role = "admin"` but, again, no CRUD endpoint
  ships in Phase 1.2. Same situation as groups.
- **`public` writes** are super_admin-only by design. Promote yourself
  via `BOOTSTRAP_SUPER_ADMIN_EMAIL` if you legitimately need to put
  shared knowledge there.
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
