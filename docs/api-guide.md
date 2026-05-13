# Memory Service API Guide

A working tour of the v0.1 API. Every block here is a real curl you can
paste; the service is live at `http://localhost:8000` after `scripts/dev.sh`.

The auto-generated Swagger UI at `/docs` mirrors this exhaustively —
use that for parameter shapes; this doc focuses on **flows** an agent
or operator actually performs.

---

## 0. Conventions

- Base URL: `http://localhost:8000` (or whatever `MS_PORT` you set).
- Auth: every business endpoint accepts either:
  - `Authorization: Bearer <jwt>` from `/v1/auth/login` — for humans/console,
  - `X-API-Key: mk_...` from `/v1/users/me/api-keys` — for agents.
  - The API key wins when both are present.
- Errors follow RFC 7807-ish JSON: `{"detail": "..."}`.
- Times are ISO 8601 in UTC.

---

## 1. Create an account

```bash
curl -X POST http://localhost:8000/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"correct-horse-battery","display_name":"You"}'
```

If your email matches the server's `BOOTSTRAP_SUPER_ADMIN_EMAIL`, you
get `is_super_admin: true` automatically.

## 2. Log in (humans)

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"correct-horse-battery"}' | jq -r .access_token)
```

JWTs last 24 hours by default (`SESSION_LIFETIME_MINUTES`). They cannot
be revoked server-side in v0.1 — clients drop them.

## 3. Mint an API key (agents)

```bash
KEY=$(curl -s -X POST http://localhost:8000/v1/users/me/api-keys \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_name":"english-agent"}' | jq -r .key)
echo "$KEY"     # mk_... — shown ONCE, store it
```

The plaintext key is returned only in this response. Subsequent
`GET /v1/users/me/api-keys` returns only the `prefix` for identification.

Revoke:

```bash
KEY_ID=...  # from the create response or GET .../api-keys
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/v1/users/me/api-keys/$KEY_ID
```

Revoked keys keep their row (for audit); auth rejects them.

---

## 4. Namespaces

Every Episode lives in exactly one namespace. PRD §4.2:

| Form | Who can read | Who can write |
|---|---|---|
| `user:<uuid>` | the user, super_admin | the user, super_admin |
| `user:me` | (alias for caller's own `user:<id>`) | same |
| `group:<slug>` | group members | group members |
| `org:<slug>` | org members | org admins |
| `public` | any authenticated caller | super_admin only |

Tips:

- Leave the field blank when writing — defaults to `user:me`.
- `user:me` is a convenience alias; agents don't have to look up their
  own UUID first.
- `group:<slug>` and `org:<slug>` work once you wire up members via
  `/v1/orgs` and `/v1/groups` (see §10).

---

## 5. Write a memory (`/v1/episodes`)

```bash
curl -X POST http://localhost:8000/v1/episodes \
  -H "X-API-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Jerry switched English teachers. The new teacher is Sarah.",
    "source_type": "text",
    "tags": ["english-learning"]
  }'
```

What happens server-side, in order:

1. Permission check on `namespace` (default `user:me`).
2. Hand the body to **Graphiti** which:
   - Calls the configured LLM to extract entities + facts.
   - Calls the configured embedder to vectorise the new nodes.
   - Writes to FalkorDB with timestamps so future queries can do
     time-aware reasoning.
3. Persist the application-layer `EpisodeMetadata` row (who/when/tags).
4. Return the new `episode_id` + the extracted entities/facts.

Latency on a CPU-bound local stack is ~2–5 s (LLM dominates).

Optional fields:

| Field | Meaning |
|---|---|
| `namespace` | Where this episode lives. Default `user:me`. |
| `source_type` | `text` (default), `message`, `json`. Hints to Graphiti's extractor. |
| `source_description` | Free-form. Defaults to the agent name on your API key. |
| `reference_time` | "When this fact is true". Default = now. Used by temporal reasoning. |
| `tags` | List of strings, free-form. |

### Known sharp edges

- **Avoid `9am` / `10pm` style timestamps in content.** Graphiti's
  internal full-text search hits a RediSearch syntax error on these
  ("`am`/`pm` after a digit"). Use `9 AM`, `morning`, etc. Bug is
  upstream in `graphiti-core`.

---

## 6. Search memory (`/v1/search`)

```bash
curl -G http://localhost:8000/v1/search \
  --data-urlencode 'query=Who is Jerry'\''s English teacher?' \
  -H "X-API-Key: $KEY"
```

Defaults search across **every namespace you can read** —
`user:<self>` + `public` + any groups/orgs you belong to. Pin
explicitly with `namespaces=`:

```bash
curl -G http://localhost:8000/v1/search \
  --data-urlencode 'query=teacher' \
  --data-urlencode 'namespaces=user:me,public' \
  -H "X-API-Key: $KEY"
```

Response shape:

```json
{
  "facts": [
    {
      "fact": "Jerry has English teacher Sarah",
      "kind": "fact",
      "namespace": "user:2a4068c5-…",
      "source_episode_ids": ["abc…"],
      "valid_at":   "2026-05-12T00:00:00Z",
      "invalid_at": null,
      "score": null
    },
    {
      "fact": "Sarah is Jerry's English teacher and lives in Toronto.",
      "kind": "entity",
      "namespace": "user:2a4068c5-…",
      "source_episode_ids": [],
      "valid_at": null,
      "invalid_at": null,
      "score": null
    }
  ],
  "query": "...",
  "namespaces_searched": []
}
```

`kind` distinguishes the two flavors of hit:

- `"fact"` — a relationship edge between entities ("A has-teacher B"). Has
  `valid_at` / `invalid_at` and `source_episode_ids`.
- `"entity"` — an entity-node summary surfaced as text. `valid_at` /
  `invalid_at` are always null; `source_episode_ids` is empty. These
  exist because Graphiti sometimes captures information in the entity's
  summary without producing an edge for it (common on short or terse
  utterances), and we don't want that to be invisible at recall time.

`invalid_at != null` means Graphiti has decided this fact has been
superseded (e.g. you later wrote "Jerry's teacher is Mike now"). The
old fact is kept so you can do time-travel queries.

---

## 7. Browse, retrieve, delete

```bash
# Newest 20 of yours
curl -H "X-API-Key: $KEY" http://localhost:8000/v1/episodes?limit=20

# Pin to a namespace
curl -H "X-API-Key: $KEY" "http://localhost:8000/v1/episodes?namespace=user:me"

# Single episode detail — original content, extracted entities + facts
curl -H "X-API-Key: $KEY" http://localhost:8000/v1/episodes/<episode_id>

# Delete (creator or super_admin only)
curl -X DELETE -H "X-API-Key: $KEY" http://localhost:8000/v1/episodes/<episode_id>
```

`DELETE` removes the Graphiti episodic node and detaches every entity
node that was only referenced by this episode. Facts derived solely
from this episode also drop.

---

## 8. Health

```bash
curl http://localhost:8000/v1/health
```

Returns 200 always; check `status` and `checks.<component>.ok`. Useful
for monitoring — a degraded `graphiti.llm` doesn't HTTP-fail the whole
service, so per-component status is the truth.

---

## 9. Where to look next

- **Examples**: `examples/english_agent_minimal.py` — a 60-line Python
  showing the full agent-side pattern (write on every turn, search
  before responding).
- **Permissions deep-dive**: `docs/permissions.md`.
- **Swagger**: `http://localhost:8000/docs`.

---

## 10. Orgs and groups

Orgs and groups give you shared `org:<slug>` / `group:<slug>` namespaces
on top of the per-user default. Quick flow:

```bash
# (super_admin only) create an org
curl -X POST http://localhost:8000/v1/orgs \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"slug":"acme","name":"Acme Corp"}'

# add a user to it; pass is_org_admin=true if they should manage the org
curl -X POST http://localhost:8000/v1/orgs/acme/members \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"user_id":"<uuid>","is_org_admin":false}'

# any org member can create a group; creator becomes group admin
curl -X POST http://localhost:8000/v1/groups \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"slug":"engineers","name":"Engineering"}'

# group admins (or org admin / super_admin) invite others
curl -X POST http://localhost:8000/v1/groups/engineers/members \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"user_id":"<uuid>","role":"member"}'
```

Once seated, write into the shared namespace:

```bash
curl -X POST http://localhost:8000/v1/episodes \
  -H "X-API-Key: $KEY" \
  -d '{"namespace":"group:engineers","content":"Our service uses Redis 7."}'
```

Permission rules in one sentence: any group member can write to
`group:<slug>`; only org admins can write to `org:<slug>`; everyone in
the org can read both. Full matrix in `docs/permissions.md`.
