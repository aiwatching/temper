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

### Remote server (docker-only, zero native deps)

If all you have is a fresh box with Docker installed and you don't want
to install Python / Postgres / FalkorDB on it:

```bash
git clone <repo> temper && cd temper
./deploy.sh                  # first run: writes .env, prompts you to fill keys
vi .env                      # set LLM_API_KEY + EMBEDDING_API_KEY + POSTGRES_PASSWORD
./deploy.sh                  # second run: docker compose up -d --build
```

`deploy.sh` subcommands: `up` (default) / `restart` / `stop` /
`logs` / `status` / `update` / `reset` (wipes volumes).

Bootstrap defaults are production-ish: `APP_ENV=production`,
`LOG_FORMAT=json`, `MS_BIND=0.0.0.0` (so the host's external IP works),
`ALLOW_SELF_REGISTRATION=false`. Change `DEFAULT_ADMIN_PASSWORD` before
exposing the port.

### Fresh machine — three scripts (local dev)

```bash
./install.sh        # one-time setup: uv + Python + deps + .env +
                    # Postgres + FalkorDB + migrations.
./start.sh          # start TEMPER in the background (default).
                    # Stop: ./start.sh stop
                    # Logs: ./start.sh logs
./update.sh         # pull + reinstall + restart (run after `git pull`).
```

All three are idempotent — safe to re-run.

`./start.sh` subcommands:

  - `start` (default) — background, PID in `.data/temper.pid`,
    log at `.data/logs/temper.log` (auto-rotates at 50 MB,
    keeps last 5)
  - `stop` — graceful SIGTERM, falls back to SIGKILL after 10s
  - `restart` — stop + start
  - `status` — running? PID + start time + log size
  - `logs [-n 500] [--no-follow]` — tail (and follow) the log
  - `start --fg` — foreground (Ctrl-C to stop, for debugging)

For foreground muscle-memory, `scripts/dev.sh` is a thin wrapper
that does `./start.sh start --fg`.

`./install.sh` flags:
  - `--reset` — wipe the dev DB volume and start over
  - `--no-docker` — skip docker compose; verifies BYO Postgres +
    FalkorDB reachability then runs migrations.
    See **`docs/deploy-no-docker.md`** for prod deployment (native
    Postgres + FalkorDB + systemd + nginx + backups).

Default admin (first boot only): `admin@example.com / admin`.
Change it via `/admin/me` after first login.

### Updating

```bash
./update.sh
```

`git pull --ff-only` + `./install.sh` (picks up new migrations / deps)
+ `./start.sh restart` (only if the service was already running).

Flags:
  - `--no-restart` — pull + install but don't bounce the service
  - `--no-pull` — install + restart only (skip git pull)

### Manual / advanced

If you'd rather drive each step yourself:

```bash
uv sync                                  # python deps
scripts/start_postgres.sh                # dev Postgres on :5432
scripts/start_falkordb.sh                # dev FalkorDB on :6380
uv run alembic upgrade head              # schema
./start.sh                               # serve
```

### Full container stack (closer to prod)

```bash
cp .env.example .env
# edit POSTGRES_PASSWORD + SECRET_KEY + LLM keys in .env
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
`DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD` in `.env`).
Log in with those, then create your real user via
`/admin/users` or `POST /v1/auth/register`.

### Reset everything and start over

```bash
./install.sh --reset      # destructive — wipes the dev DB volume
```

## License

MIT
