# Memory Service

Multi-tenant central memory layer for AI agents. Self-hosted Zep-style service
backed by [Graphiti](https://github.com/getzep/graphiti) on FalkorDB.

> **Status:** v0.1 / Phase 1.0–1.1 scaffold. Most endpoints stubbed.
> See `docs/v.05-pr.md` for the full PRD and roadmap.

## Components

```
┌──────────────────┐
│ Agent (any lang) │
└────────┬─────────┘
         │ HTTP REST  /v1/...
         ▼
┌──────────────────┐       ┌──────────────┐
│ FastAPI app      │──────▶│ PostgreSQL   │ users / api keys / episode meta
│ (this repo)      │       │              │ + memory_blocks (KV memory)
│                  │       └──────────────┘
│                  │       ┌──────────────┐
│                  │──────▶│ FalkorDB     │ Graphiti knowledge graph
└────────┬─────────┘       └──────────────┘
         │
         ▼
   OpenAI API (entity extraction + embeddings)
```

Two memory primitives:

- **Graph (episodes + entities + facts)** — emergent recall over many
  episodes, bi-temporal, good for third-party facts. See `docs/api-guide.md`.
- **Memory blocks (KV)** — structured key/value, for first-person
  assertions Graphiti is bad at (nicknames, preferences, current state).
  See `docs/memory-blocks.md`.

A third primitive (Documents / markdown) is deferred — see `docs/vision.md`.

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

## Layout

```
src/memory_service/
├── main.py            FastAPI entry
├── config.py          Pydantic Settings
├── api/v1/            HTTP routes (auth, users, episodes, search, system, ...)
├── core/              Business logic (auth, permissions, namespaces, memory)
├── adapters/          External clients (graphiti, falkordb, openai)
├── models/            SQLAlchemy ORM
├── schemas/           Pydantic request/response schemas
├── db/                Engine + Alembic migrations
└── web/               Admin page (Jinja2 templates + static)
```

## Roadmap

The PRD (`docs/v.05-pr.md`) defines 8 phases (1.0 → 1.8). The repo currently
covers 1.0–1.1 (skeleton, infra, health endpoint, admin page stub). Subsequent
phases land in subsequent commits.

## License

MIT
