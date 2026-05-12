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

### With Docker Compose (recommended)

```bash
cp .env.example .env
# Edit .env: put a real OPENAI_API_KEY and SECRET_KEY
docker compose up --build
```

Once everything is healthy:

```bash
curl http://localhost:8000/v1/health
open http://localhost:8000/admin   # simple management page
```

### Local development (without Docker)

You still need PostgreSQL and FalkorDB available locally. Easiest:

```bash
docker compose up -d db falkordb
```

Then:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn memory_service.main:app --reload
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
