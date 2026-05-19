# TEMPER

Multi-tenant central memory layer for AI agents. Self-hosted Zep-style service
backed by [Graphiti](https://github.com/getzep/graphiti) on FalkorDB +
Postgres for structured state + documents.

## Components

```
┌──────────────────┐
│ Agent (any lang) │
└────────┬─────────┘
         │ HTTP REST  /v1/...
         ▼
┌──────────────────┐       ┌──────────────┐
│ FastAPI app      │──────▶│ PostgreSQL   │ users / api keys / episode meta
│ (this repo)      │       │              │ + memory_blocks   (KV memory)
│                  │       │              │ + documents       (markdown wiki)
│                  │       │              │ + scheduled jobs  (per-agent)
│                  │       └──────────────┘
│                  │       ┌──────────────┐
│                  │──────▶│ FalkorDB     │ Graphiti knowledge graph
└────────┬─────────┘       └──────────────┘
         │
         ▼
   LLM provider (OpenAI / Anthropic / corp gateway — extraction + embeddings)
```

### Four memory primitives

| | What it stores | Use it for |
|---|---|---|
| **Episodes** (graphiti) | short structured facts, extracted into entities + edges | "Bob is on the auth team", "we shipped X last sprint" |
| **Blocks** (KV) | first-person assertions in JSONB | nicknames, preferences, current focus, active tasks |
| **Documents** (markdown) | long-form addressable content with wikilinks | saved tickets, meeting notes, SOPs, agent-written reports |
| **Jobs** (scheduled) | recurring or future-triggered LLM prompts | "every morning send standup", "alert on plugin failure" |

A **typed memory layer** sits on top: `task_add` / `set_focus` /
`set_preference` / `note_event` / `note_save` / `schedule_job` etc.
Agents pick intent by picking the function name; TEMPER decides which
primitive lands the write. See `docs/agent-integration-prompt.md` for
the canonical routing contract agents embed in their system prompt.

## Quick start

### Fresh machine — one command does everything

```bash
./install.sh        # detect platform, install uv if needed, sync deps,
                    # write .env.local, boot Postgres + FalkorDB, run
                    # migrations.
scripts/dev.sh      # launch uvicorn with auto-reload.
```

The installer is idempotent — safe to re-run after pulling new
commits to pick up dep / migration changes.

Useful flags:
  - `./install.sh --reset` — wipe the dev DB volume and start over
  - `./install.sh --no-docker` — skip the DB setup if you have your
    own Postgres + FalkorDB to point at via `DATABASE_URL`

Default admin (first boot only): `admin@example.com / admin`.
Change it via `/admin/me` after first login.

### Manual / advanced

If you'd rather drive each step yourself:

```bash
uv sync                                  # python deps
scripts/start_postgres.sh                # dev Postgres on :5432
scripts/start_falkordb.sh                # dev FalkorDB on :6380
uv run alembic upgrade head              # schema
scripts/dev.sh                           # serve
```

### Full container stack (closer to prod)

```bash
cp .env.example .env.prod
# edit POSTGRES_PASSWORD + SECRET_KEY + LLM keys in .env.prod
docker compose up --build
```

## For agent integrators

If you're plugging an agent into TEMPER, start here:

- **`docs/agent-integration-prompt.md`** — the canonical routing
  contract. Embed it verbatim in your agent's system prompt.
- **`docs/api-guide.md`** — REST surface (write, search, blocks,
  documents, jobs, turn_context).
- **`docs/memory-blocks.md`** — KV memory design + use cases.
- **`docs/memory-frameworks-comparison.md`** — why graphiti vs mem0,
  and when each one's the right primitive.
- **`examples/english_agent_chat.py`** — single-file working agent
  using the recall → prompt → reply → remember loop.

A reference agent (Smith) lives at `agents/smith/`. It's optional —
TEMPER itself stands alone.

## Layout

```
.
├── install.sh                  one-command setup (this file does most of the work)
├── docker-compose.yml          prod-style stack (postgres + falkordb + service)
├── docker-compose.dev.yml      dev override: exposes DB ports to host
├── scripts/
│   ├── dev.sh                  start uvicorn with auto-reload
│   ├── start_postgres.sh       bring up dev Postgres on localhost:5432
│   ├── start_falkordb.sh       bring up dev FalkorDB on localhost:6380
│   └── start_embedding.sh      local embedding backend (ollama)
│
├── src/memory_service/
│   ├── main.py                 FastAPI entry
│   ├── config.py               Pydantic Settings
│   ├── api/v1/                 HTTP routes — auth, users, episodes, search,
│   │                           blocks, documents, typed_memory, system, ...
│   ├── core/                   Business logic — auth, permissions, namespaces,
│   │                           memory, blocks, documents, typed_memory
│   ├── adapters/               External clients (graphiti, falkordb, openai)
│   ├── models/                 SQLAlchemy ORM — User / Episode / Block /
│   │                           Document / DocumentLink / DocumentRevision / ...
│   ├── schemas/                Pydantic request/response shapes
│   ├── db/                     Engine + Alembic migrations
│   └── web/                    Admin page (Jinja2 + Alpine.js)
│
├── docs/                       all design docs + the integration prompt
├── examples/                   working sample agents (Python httpx)
├── agents/
│   └── smith/                  reference agent — pi-coding-agent + TEMPER client
└── tests/                      integration + unit
```

## Troubleshooting

### `self-signed certificate in certificate chain` during install

You're on a TLS-inspecting corporate network (the proxy MITMs HTTPS).
TEMPER's installer auto-detects and retries with `uv --native-tls`
which uses the system trust store (your corp CA is usually there).
For Node-side things (Smith), set:

```bash
export NODE_EXTRA_CA_CERTS=/path/to/corp-ca.pem
```

For Docker pulls, configure Docker Desktop's certificate store
(macOS keychain integration usually handles it).

### `DatatypeMismatch: incompatible types: uuid and character varying`

You're running an old migration on Postgres. Pull latest commits;
0012/0013 use `VARCHAR(36)` consistently (matches the rest of the
schema). Then `uv run alembic upgrade head`.

### Default admin login doesn't work

The Postgres docker volume is fresh — your old SQLite-era users
didn't migrate. On first boot TEMPER auto-creates
`admin@example.com / admin` (configurable via
`DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD` in `.env.local`).
Log in with those, then create your real user via
`/admin/users` or `POST /v1/auth/register`.

### Reset everything and start over

```bash
./install.sh --reset      # destructive — wipes the dev DB volume
```

## License

MIT
