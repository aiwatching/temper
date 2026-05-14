# memctl

Terminal client for the Memory Service. Same REST surface as the
examples, just packaged as `memctl <subcommand>` with a config file so
you don't have to keep pasting `-H "X-API-Key: mk_..."`.

It ships as a `[project.scripts]` entry, so `pip install -e .` (the
normal dev install) puts `memctl` on `$PATH`.

## Quick start

```bash
# 1. point at your service (defaults to http://localhost:18088)
memctl --base-url http://localhost:18088 login --email you@example.com
# OR if you already have an API key
memctl set-key mk_yourkeyhere

# 2. use it
memctl whoami
memctl write "Sarah teaches me English."
memctl search teacher
memctl ls --limit 10
memctl show <episode_id>
memctl rm <episode_id>
```

## Auth resolution

Each command picks credentials in this order (first hit wins):

1. `--key mk_...` / `--token <jwt>` on the command line
2. `MEMCTL_API_KEY` / `MEMCTL_JWT` env vars
3. `MS_API_KEY` env var (matches the `examples/` convention)
4. `~/.config/memctl/config.toml`

Base URL has its own precedence: `--base-url` → `MEMCTL_BASE_URL` →
`MS_BASE_URL` → config file → `http://localhost:18088`.

The config file is created by `login` and `set-key` and is chmod 0600.
Wipe with `memctl logout`.

## Subcommand reference

### Episodes

| Command | Purpose |
|---|---|
| `memctl write CONTENT [-n NS] [--source text\|message\|json] [--tags ...]` | Add an episode |
| `memctl search QUERY [-n NS] [--limit N]` | Semantic search (returns facts + entity summaries) |
| `memctl ls [-n NS] [--limit N]` | Most-recent episodes |
| `memctl show EPISODE_ID` | Detail: content, entities, facts |
| `memctl rm EPISODE_ID` | Delete |

### Identity

| Command | Purpose |
|---|---|
| `memctl login [--email E] [--password P]` | Interactive login, stores JWT |
| `memctl set-key KEY` | Save an API key into config |
| `memctl whoami` | Current user record |
| `memctl logout` | Drop the stored config |

### API keys

| Command | Purpose |
|---|---|
| `memctl key create [--name NAME]` | Mint a new `mk_...` key (shown once) |
| `memctl key ls` | List your keys (no plaintext) |
| `memctl key rm KEY_ID` | Revoke |

### Orgs (super_admin to create)

| Command | Purpose |
|---|---|
| `memctl org create SLUG --name NAME` | Create an org |
| `memctl org ls` | Orgs you can see |
| `memctl org rm SLUG` | Delete |
| `memctl org member add SLUG USER_ID [--admin]` | Add user to org (optionally as org_admin) |
| `memctl org member ls SLUG` | List org members |
| `memctl org member rm SLUG USER_ID` | Remove (or self-leave) |

### Groups (any org member can create)

| Command | Purpose |
|---|---|
| `memctl group create SLUG --name NAME [--org SLUG]` | Create a group in your org (or any org as super_admin) |
| `memctl group ls` | Groups you can see |
| `memctl group rm SLUG` | Delete (group admin / org_admin / super_admin) |
| `memctl group member add SLUG USER_ID [--admin]` | Add a member |
| `memctl group member ls SLUG` | List group members |
| `memctl group member rm SLUG USER_ID` | Remove (or self-leave) |

### Graph inspection (direct FalkorDB)

These talk to FalkorDB directly (`localhost:6380` by default), bypassing
the service. Useful for debugging "where did my data go?" / "did the
LLM extract any edges?". They're read-only by construction (`ro_query`).

| Command | Purpose |
|---|---|
| `memctl graph list` | All graphs in FalkorDB with node/edge counts |
| `memctl graph summary [-n NS]` | Per-label counts for a namespace's graph |
| `memctl graph nodes [-n NS] [--label LABEL] [--limit N]` | Walk through nodes (Episodic, Entity, Community) |
| `memctl graph edges [-n NS] [--limit N]` | Walk through RELATES_TO edges (facts) |
| `memctl graph cypher [-n NS] 'CYPHER'` | Arbitrary read-only Cypher query |

Override the FalkorDB target with `--falkordb-host` / `--falkordb-port`
flags or `FALKORDB_HOST` / `FALKORDB_PORT` env vars. `--namespace`
defaults to `user:me`.

For a visual interface, the FalkorDB image bundles a Browser UI at
http://localhost:3000/ (published by `scripts/start_falkordb.sh`).

## Scripting

Every command takes `--json` to emit the raw API response instead of a
table. Combine with `jq` or pipe into Python:

```bash
# Newest episode id
memctl --json ls --limit 1 | jq -r .episodes[0].episode_id

# Wait until a search hit appears
while ! memctl --json search teacher | jq -e '.facts | length > 0' >/dev/null; do
  sleep 1
done
```

## Internals

Implementation: `src/memory_service/memctl.py` — single file, stdlib +
`httpx` only. Read it as a complete REST client example if you want to
port the pattern to another language.
