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
│ (this repo)      │       └──────────────┘
│                  │       ┌──────────────┐
│                  │──────▶│ FalkorDB     │ Graphiti knowledge graph
└────────┬─────────┘       └──────────────┘
         │
         ▼
   OpenAI API (entity extraction + embeddings)
```

## Quick start

### One-shot (recommended for local dev)

```bash
cp .env.example .env
# Edit .env: put your LLM provider keys and SECRET_KEY
scripts/dev.sh
```

`scripts/dev.sh` is idempotent — it creates `.venv` if missing, installs
deps only when `pyproject.toml` changes, brings up the local embedding
backend via `scripts/start_embedding.sh`, and starts uvicorn with an
auto-managed SQLite dev DB. Re-run as often as you want.

Once started, the browser opens automatically at `http://localhost:18088/admin`.

### With Docker Compose (full stack including Postgres + FalkorDB)

```bash
cp .env.example .env
docker compose up --build
```

### Postgres parity for dev

```bash
docker compose up -d db
DATABASE_URL="postgresql+asyncpg://memory:memory@localhost:5432/memory_service" \
  scripts/dev.sh
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
