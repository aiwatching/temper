#!/usr/bin/env bash
# scripts/dev.sh
#
# One-shot local dev launcher. Idempotent — safe to re-run.
#
# Steps:
#   1. Ensure .venv exists and the project is installed editable.
#   2. Ensure the anthropic extra is installed (no-op when already present).
#   3. Bring up the local embedding backend via start_embedding.sh.
#   4. Start uvicorn with a SQLite dev database under .data/.
#
# Usage:
#   scripts/dev.sh                  # default: 127.0.0.1:18088, opens browser
#   MS_PORT=8080 scripts/dev.sh     # custom port
#   MS_HOST=0.0.0.0 scripts/dev.sh  # bind all interfaces
#   OPEN_BROWSER=0 scripts/dev.sh   # don't auto-open /admin
#
# We deliberately use MS_PORT/MS_HOST (not PORT/HOST) because other tools
# like Forge inject a global PORT into the shell environment and would
# otherwise hijack the bind address.
#
# Postgres mode (for parity with prod):
#   docker compose up -d db
#   DATABASE_URL="postgresql+asyncpg://memory:memory@localhost:5432/memory_service" \
#     scripts/dev.sh

set -euo pipefail
cd "$(dirname "$0")/.."

# Use a memory-service-specific env name so we don't collide with PORT/HOST
# that other tools (e.g. Forge sets PORT=8403 globally) might export.
PORT="${MS_PORT:-18088}"
HOST="${MS_HOST:-127.0.0.1}"

say()   { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$*"; }

# Stop the ollama process we started (if any) on shutdown. The pid file
# is only written by start_embedding.sh when *we* started ollama —
# if it was already running before dev.sh ran, we leave it alone.
cleanup() {
  if [[ -f /tmp/temper-ollama.pid ]]; then
    local pid
    pid="$(cat /tmp/temper-ollama.pid 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      printf '\n  stopping ollama (pid=%s)...\n' "$pid"
      kill "$pid" 2>/dev/null || true
    fi
    rm -f /tmp/temper-ollama.pid
  fi
}
trap cleanup EXIT INT TERM

# ---- 1. venv + deps ------------------------------------------------------
#
# uv-driven now: no system python3 / pip dance, no manual .venv create.
# `uv sync` creates .venv on first run, picks an interpreter from uv's
# managed pool (matching pyproject.toml's requires-python), installs
# pinned deps from uv.lock. Subsequent runs are no-ops when nothing
# changed.
#
# Run install.sh once (or any time pyproject.toml / uv.lock changes);
# dev.sh just calls `uv sync` here to be defensive in case someone
# launched dev.sh on a fresh checkout without running install.sh first.

say "Python environment"

UV_FLAGS=()
if ! uv sync --dry-run >/dev/null 2>&1; then
  # Default TLS chain failed — common on corporate networks with a
  # self-signed CA in the chain. --native-tls uses the system trust
  # store, which usually has the corp CA imported already.
  UV_FLAGS+=(--native-tls)
fi
uv sync "${UV_FLAGS[@]}" --quiet
ok "deps in sync (.venv via uv)"

# graphiti's anthropic extra isn't part of the pinned set; install on
# demand so the LLM_PROVIDER=anthropic path works without surprises.
if ! uv run python -c 'import anthropic' >/dev/null 2>&1; then
  uv pip install "${UV_FLAGS[@]}" --quiet "graphiti-core[anthropic]"
fi

# ---- 2. embedding backend ------------------------------------------------

say "Embedding backend"
scripts/start_embedding.sh

# ---- 3a. Postgres --------------------------------------------------------

say "Postgres"
scripts/start_postgres.sh

# ---- 3b. FalkorDB (Graphiti graph store) ---------------------------------

say "FalkorDB"
scripts/start_falkordb.sh

# ---- 4. database URL -----------------------------------------------------

say "Database"
mkdir -p .data
# TEMPER is now Postgres-only — documents primitive uses JSONB / ARRAY
# / TSVECTOR / GIN / plpgsql triggers that SQLite can't render. Default
# to the docker-compose Postgres on localhost:5432. Override with
# DATABASE_URL env if your Postgres lives elsewhere.
PG_USER="${POSTGRES_USER:-memory}"
PG_PASS="${POSTGRES_PASSWORD:-memory}"
PG_DB="${POSTGRES_DB:-memory_service}"
DEFAULT_DB="postgresql+asyncpg://${PG_USER}:${PG_PASS}@localhost:5432/${PG_DB}"
export DATABASE_URL="${DATABASE_URL:-$DEFAULT_DB}"

if [[ "$DATABASE_URL" == sqlite* ]]; then
  echo
  echo "  ⚠️  SQLite is no longer supported (documents primitive needs"
  echo "      Postgres-only features). Bring Postgres up:"
  echo
  echo "          docker compose up -d db"
  echo "          alembic upgrade head"
  echo
  echo "      Or set DATABASE_URL to point at your own Postgres."
  echo
  exit 1
fi
ok "using $DATABASE_URL"

# Probe before uvicorn so a missing/wrong DB fails fast with a
# readable hint instead of mid-startup SQLAlchemy stack trace.
if ! pg_isready -h "$(echo "$DATABASE_URL" | sed -E 's#.*@([^:/]+).*#\1#')" \
                -p "$(echo "$DATABASE_URL" | sed -E 's#.*:([0-9]+)/.*#\1#')" \
                >/dev/null 2>&1; then
  echo
  echo "  ⚠️  Can't reach Postgres at $DATABASE_URL"
  echo "      Start it:  docker compose up -d db"
  echo "      Or override DATABASE_URL=postgresql+asyncpg://user:pass@host:port/db"
  echo
  # Don't exit hard — pg_isready may be missing on macOS without
  # postgres-client installed. Just warn and continue; uvicorn will
  # surface the real connection error on first request.
fi

# ---- 5. open browser & start uvicorn -------------------------------------

if [[ "${OPEN_BROWSER:-1}" = "1" ]]; then
  ( sleep 2 && command -v open >/dev/null && open "http://$HOST:$PORT/admin" ) &
fi

say "Starting uvicorn on http://$HOST:$PORT"
echo "  Admin:   http://$HOST:$PORT/admin"
echo "  Login:   http://$HOST:$PORT/admin/login"
echo "  Account: http://$HOST:$PORT/admin/me"
echo "  Docs:    http://$HOST:$PORT/docs"
echo "  Health:  http://$HOST:$PORT/v1/health"
echo
echo "  Press Ctrl-C to stop."
echo

# --reload watches only Temper's Python source. Without --reload-dir, uvicorn
# defaults to the cwd which now includes agents/smith/ — Smith file changes
# would otherwise trigger a Temper restart. Templates and static assets are
# served fresh from disk each request, so they don't need uvicorn reloads.
exec uv run "${UV_FLAGS[@]}" uvicorn memory_service.main:app --reload \
  --reload-dir src/memory_service \
  --host "$HOST" --port "$PORT"
