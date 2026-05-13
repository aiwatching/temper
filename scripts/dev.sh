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
#   scripts/dev.sh                # default: localhost:8000, opens browser
#   PORT=8080 scripts/dev.sh      # custom port
#   OPEN_BROWSER=0 scripts/dev.sh # don't auto-open /admin
#
# Postgres mode (for parity with prod):
#   docker compose up -d db
#   DATABASE_URL="postgresql+asyncpg://memory:memory@localhost:5432/memory_service" \
#     scripts/dev.sh

set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"

say()   { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$*"; }

# ---- 1. venv + deps ------------------------------------------------------

say "Python environment"

if [[ ! -d .venv ]]; then
  say "creating .venv"
  python3 -m venv .venv
  ok ".venv created"
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# Reinstall deps only when pyproject.toml changed since last successful install.
STAMP=.venv/.deps-installed
NEED_INSTALL=0
if [[ ! -f "$STAMP" ]]; then NEED_INSTALL=1
elif [[ pyproject.toml -nt "$STAMP" ]]; then NEED_INSTALL=1
fi
if [[ "$NEED_INSTALL" = "1" ]]; then
  say "installing dependencies (this may take a minute)"
  pip install --quiet --upgrade pip
  pip install --quiet -e ".[dev]"
  pip install --quiet "graphiti-core[anthropic]"
  touch "$STAMP"
  ok "deps installed"
else
  ok "deps already installed (pyproject.toml unchanged)"
fi

# ---- 2. embedding backend ------------------------------------------------

say "Embedding backend"
scripts/start_embedding.sh

# ---- 3. database URL -----------------------------------------------------

say "Database"
mkdir -p .data
DEFAULT_DB="sqlite+aiosqlite:///$(pwd)/.data/ms-dev.db"
export DATABASE_URL="${DATABASE_URL:-$DEFAULT_DB}"
if [[ "$DATABASE_URL" == sqlite* ]]; then
  ok "using SQLite at $DATABASE_URL"
  ok "(reset with: rm .data/ms-dev.db)"
else
  ok "using $DATABASE_URL"
fi

# ---- 4. open browser & start uvicorn -------------------------------------

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

exec uvicorn memory_service.main:app --reload --host "$HOST" --port "$PORT"
