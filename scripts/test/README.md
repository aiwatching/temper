# Test scripts

Quick API smoke + load tests for whatever's currently shipped.

## smoke.sh

End-to-end exercise of every Phase 1.2 endpoint, with negative paths
(bad key, duplicate email, cross-user revoke).

```bash
API_KEY=mk_your_key_here scripts/test/smoke.sh
```

Optional: `BASE=http://...` to point at a non-default host.

Exits 0 if everything passes, 1 with a summary line otherwise.

## load.py

Light concurrent hammer on `/v1/auth/me` (the cheapest authenticated
endpoint). Use it to spot DB session leaks / async deadlocks /
per-request overhead regressions.

```bash
source .venv/bin/activate          # for httpx
API_KEY=mk_your_key_here python3 scripts/test/load.py -n 500 -c 50
```

Output: throughput, status-code histogram, p50 / p95 / p99 latencies.

## When does this break?

Both scripts target the surface implemented at this point in the PRD:

- `/v1/health`
- `/v1/auth/{register,login,logout,me}`
- `/v1/users/me/api-keys[/{id}]`

When Phase 1.5 (episode write) and 1.6 (search) land, this dir gets a
`memory.sh` or similar.
