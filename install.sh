#!/usr/bin/env bash
# install.sh — one-command setup for TEMPER.
#
# Use for both:
#   * fresh clone on a brand-new machine
#   * "reset to clean state" on an existing machine (safe to re-run)
#
# What it does:
#   1. Detect platform + check required tools (python3, docker, uv)
#   2. Set up .venv via uv sync (creates if missing, syncs deps)
#   3. Write .env.local with sensible defaults if missing
#   4. Boot Postgres + FalkorDB containers (docker compose)
#   5. Run alembic upgrade head against the dev Postgres
#   6. Print next steps (start command, admin creds, key URLs)
#
# Re-running is idempotent. Skips bits already done.
#
# Flags:
#   --reset      Drop the existing dev Postgres volume + start fresh.
#                Wipes ALL data — use to recover from migration drift
#                or to test the install flow end-to-end.
#   --no-docker  Skip Postgres/FalkorDB startup. Use when you point
#                DATABASE_URL at your own Postgres elsewhere.
#   -h / --help  This message.
#
# Quick start:
#   ./install.sh        # set everything up
#   scripts/dev.sh      # start TEMPER

set -euo pipefail
cd "$(dirname "$0")"

# ---- ANSI helpers --------------------------------------------------
if [[ -t 1 ]]; then
  C_BLUE=$'\e[34m' C_GREEN=$'\e[32m' C_YELLOW=$'\e[33m' C_RED=$'\e[31m' C_BOLD=$'\e[1m' C_RST=$'\e[0m'
else
  C_BLUE='' C_GREEN='' C_YELLOW='' C_RED='' C_BOLD='' C_RST=''
fi
step() { printf '\n%s━━ %s%s%s ━━%s\n' "${C_BLUE}" "${C_BOLD}" "$*" "${C_RST}${C_BLUE}" "${C_RST}"; }
ok()   { printf '  %s✓%s %s\n' "${C_GREEN}" "${C_RST}" "$*"; }
warn() { printf '  %s⚠%s %s\n' "${C_YELLOW}" "${C_RST}" "$*"; }
die()  { printf '  %s✗%s %s\n' "${C_RED}" "${C_RST}" "$*" >&2; exit 1; }

# ---- args ----------------------------------------------------------
RESET=0
SKIP_DOCKER=0
for arg in "$@"; do
  case "$arg" in
    --reset)     RESET=1 ;;
    --no-docker) SKIP_DOCKER=1 ;;
    -h|--help)
      sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) die "unknown arg: $arg (use --help)" ;;
  esac
done

# ---- 1. platform + tools ------------------------------------------
step "Checking system"

PLATFORM="$(uname -s)"
case "$PLATFORM" in
  Darwin)  ok "macOS detected" ;;
  Linux)   ok "Linux detected" ;;
  *)       warn "untested platform $PLATFORM — proceeding anyway" ;;
esac

# python3 (3.11+ for TEMPER)
if ! command -v python3 >/dev/null; then
  die "python3 not found. Install Python 3.11+ first."
fi
PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_MAJ="${PY_VER%.*}"; PY_MIN="${PY_VER#*.}"
if (( PY_MAJ < 3 )) || (( PY_MAJ == 3 && PY_MIN < 11 )); then
  die "Python 3.11+ required, got $PY_VER"
fi
ok "Python $PY_VER"

# uv (manages the venv + deps)
if ! command -v uv >/dev/null; then
  warn "uv not found — installing via official script"
  curl -fsSL https://astral.sh/uv/install.sh | sh
  # uv installs to ~/.local/bin or ~/.cargo/bin depending on platform
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  command -v uv >/dev/null || die "uv install failed — install manually: https://docs.astral.sh/uv/"
fi
ok "uv $(uv --version | awk '{print $2}')"

# docker (only if we'll bring up the DB containers)
if [[ "$SKIP_DOCKER" = 0 ]]; then
  if ! command -v docker >/dev/null; then
    die "docker not found. Install Docker Desktop (macOS) or docker-ce (Linux), or re-run with --no-docker."
  fi
  if ! docker info >/dev/null 2>&1; then
    die "docker is installed but not running. Start Docker Desktop / dockerd and re-run."
  fi
  ok "docker $(docker --version | awk '{print $3}' | tr -d ,)"
fi

# ---- 2. python deps -----------------------------------------------
step "Installing Python deps"

# Detect corporate self-signed CA situation (the user mentioned hitting
# this with pnpm). For uv, --native-tls uses the system trust store
# which usually contains the corp CA already.
UV_FLAGS=()
if [[ "${USE_NATIVE_TLS:-auto}" = "auto" ]]; then
  # Heuristic: if pip / curl previously needed --native-tls, set it.
  # Cheapest probe — does a trivial uv resolve work without it?
  if ! uv sync --dry-run >/dev/null 2>&1; then
    warn "default TLS chain failed — retrying with --native-tls (corporate CA?)"
    UV_FLAGS+=(--native-tls)
  fi
elif [[ "${USE_NATIVE_TLS}" = "1" ]] || [[ "${USE_NATIVE_TLS}" = "true" ]]; then
  UV_FLAGS+=(--native-tls)
fi

uv sync "${UV_FLAGS[@]}"
ok "deps installed via uv sync"

# graphiti's anthropic extra is optional but commonly wanted; install
# if it's not already pulled in. uv sync handles the base extras (dev),
# this is the one-off for the Anthropic LLM provider.
if ! uv run python -c 'import anthropic' >/dev/null 2>&1; then
  warn "anthropic extra missing — adding via uv pip install"
  uv pip install "${UV_FLAGS[@]}" "graphiti-core[anthropic]" >/dev/null
fi
ok "graphiti[anthropic] available"

# ---- 3. .env.local --------------------------------------------------
step "Configuring environment"

if [[ ! -f .env.local ]] && [[ ! -f .env ]]; then
  cat > .env.local <<'EOF'
# TEMPER local dev settings. Override via shell env to layer on top.

# DB — the dev scripts default to docker-compose Postgres. Set
# DATABASE_URL only if you want to point at your own Postgres.
# DATABASE_URL=postgresql+asyncpg://memory:memory@localhost:5432/memory_service

# Embedding backend — fastembed (default) runs locally, no API key needed.
EMBEDDING_PROVIDER=fastembed

# LLM provider — used by graphiti for extraction. Pick one:
#   openai   needs OPENAI_API_KEY
#   anthropic needs ANTHROPIC_API_KEY
#   gemini   needs GEMINI_API_KEY
#   custom   needs LLM_API_BASE + LLM_API_KEY (OpenAI-compat endpoint)
LLM_PROVIDER=openai
# OPENAI_API_KEY=sk-...

# Default admin auto-created on first boot if users table is empty.
# Change after first login.
CREATE_DEFAULT_ADMIN=true
DEFAULT_ADMIN_EMAIL=admin@example.com
DEFAULT_ADMIN_PASSWORD=admin

# Logging
LOG_LEVEL=INFO

# Allow self-registration for local dev (lets you create extra users
# via POST /v1/auth/register without admin). Off in prod.
ALLOW_SELF_REGISTRATION=true
EOF
  ok ".env.local written with defaults — edit if you want different LLM provider / admin creds"
else
  ok ".env / .env.local already present"
fi

# ---- 4. databases --------------------------------------------------
if [[ "$SKIP_DOCKER" = 1 ]]; then
  step "Skipping docker (--no-docker)"
  warn "make sure DATABASE_URL points at a reachable Postgres before running scripts/dev.sh"
else
  if [[ "$RESET" = 1 ]]; then
    step "Resetting dev Postgres volume"
    warn "this will WIPE the existing dev database"
    read -r -p "  type 'yes' to confirm: " confirm
    if [[ "$confirm" = "yes" ]]; then
      docker compose -f docker-compose.yml -f docker-compose.dev.yml down -v postgres falkordb 2>/dev/null || true
      ok "containers + volumes removed"
    else
      warn "skipping reset"
    fi
  fi

  step "Starting Postgres"
  scripts/start_postgres.sh
  ok "Postgres at localhost:5432"

  step "Starting FalkorDB"
  scripts/start_falkordb.sh
  ok "FalkorDB at localhost:6380"

  step "Running migrations"
  export POSTGRES_USER="${POSTGRES_USER:-memory}"
  export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-memory}"
  export POSTGRES_DB="${POSTGRES_DB:-memory_service}"
  export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:5432/${POSTGRES_DB}}"
  uv run "${UV_FLAGS[@]}" alembic upgrade head
  ok "schema up to date"
fi

# ---- 5. next steps -------------------------------------------------
step "Done"
cat <<EOF

  ${C_GREEN}TEMPER is installed and ready.${C_RST}

  Start:        ${C_BOLD}scripts/dev.sh${C_RST}
  Admin UI:     http://127.0.0.1:18088/admin
  API docs:     http://127.0.0.1:18088/docs
  Health:       http://127.0.0.1:18088/v1/health

  Default admin (first boot only):
    email:      admin@example.com
    password:   admin
    ${C_YELLOW}change immediately via /admin/me${C_RST}

  Common next steps:
    ${C_BOLD}vi .env.local${C_RST}              edit LLM provider / API keys
    ${C_BOLD}scripts/dev.sh${C_RST}             launch with auto-reload
    ${C_BOLD}./install.sh --reset${C_RST}       wipe + start over (destructive)
    ${C_BOLD}./install.sh --no-docker${C_RST}   skip DB setup (BYO Postgres)

EOF
