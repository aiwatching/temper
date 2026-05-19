#!/usr/bin/env bash
# install.sh — one-command setup for TEMPER.
#
# Use for both:
#   * fresh clone on a brand-new machine
#   * "reset to clean state" on an existing machine (safe to re-run)
#
# What it does:
#   1. Detect platform + check / install required tools
#       - uv (auto-installs via official script if missing)
#       - Python ≥ 3.11 via uv's managed pool (NOT system python3)
#       - docker (errors if missing — Docker Desktop / docker-ce
#         needs a manual install on most platforms)
#   2. Set up .venv via uv sync (creates if missing, syncs deps)
#   3. Write .env.local with sensible defaults if missing
#   4. Boot Postgres + FalkorDB containers (docker compose)
#   5. Run alembic upgrade head against the dev Postgres
#   6. Print next steps (start command, admin creds, key URLs)
#
# System python3 is NOT used — uv ships portable Python interpreters
# under ~/.local/share/uv/python/. So Ubuntu 22.04 (python3.10 default)
# / older RHEL / minimal containers all just work, no need to mess
# with deadsnakes PPA or update-alternatives.
#
# Override which Python version uv installs:
#   PYTHON_VERSION=3.13 ./install.sh
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

# uv (manages BOTH the Python interpreter AND deps — no system
# python3 needed). uv ships portable Python builds and stores them
# under ~/.local/share/uv/python/, so we don't touch system Python
# (Ubuntu's apt-installed python3 stays whatever version it is).
if ! command -v uv >/dev/null; then
  warn "uv not found — installing via official script"
  curl -fsSL https://astral.sh/uv/install.sh | sh
  # uv installs to ~/.local/bin or ~/.cargo/bin depending on platform
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  command -v uv >/dev/null || die "uv install failed — install manually: https://docs.astral.sh/uv/"
fi
ok "uv $(uv --version | awk '{print $2}')"

# Ensure uv has a Python ≥ 3.11 available. pyproject.toml's
# requires-python = ">=3.11" lets `uv sync` pick anything compatible
# from the managed pool. `uv python install` is idempotent — no-op
# when something compatible is already present.
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
if ! uv python find ">=3.11" >/dev/null 2>&1; then
  warn "no Python ≥ 3.11 available to uv — installing $PYTHON_VERSION"
  uv python install "$PYTHON_VERSION"
fi
ok "Python via uv: $(uv python find '>=3.11')"

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

# ---- 3. .env --------------------------------------------------
step "Configuring environment"

# config.py reads `.env`. Copy from the canonical .env.example
# (which the team keeps in sync with config.py — all keys, all
# provider hints, all defaults). Then layer dev-friendly overrides
# on top so first boot just works.
if [[ ! -f .env ]]; then
  if [[ ! -f .env.example ]]; then
    die ".env.example missing — can't bootstrap .env. Pull latest from main?"
  fi
  cp .env.example .env

  # Append dev overrides: random SECRET_KEY (otherwise JWT signing
  # uses the placeholder), permissive defaults that match a single-
  # user laptop install.
  RANDOM_SECRET="$(uv run python -c 'import secrets; print(secrets.token_urlsafe(48))' 2>/dev/null || head -c 48 /dev/urandom | base64)"
  cat >> .env <<EOF

# ───────────────────────────────────────────────────────────────
# Appended by install.sh for local dev (2026-05-17). Safe to edit.
# ───────────────────────────────────────────────────────────────

# Strong key generated at install time — DO NOT commit this file.
SECRET_KEY=${RANDOM_SECRET}

# Self-registration on for solo dev; off in prod.
ALLOW_SELF_REGISTRATION=true

# Default admin auto-created on first boot if users table is empty.
# Change password via /admin/me after first login.
CREATE_DEFAULT_ADMIN=true
DEFAULT_ADMIN_EMAIL=admin@example.com
DEFAULT_ADMIN_PASSWORD=admin
EOF
  ok ".env written from .env.example + dev overrides"
  warn "edit LLM_PROVIDER / LLM_API_KEY in .env before agent integration tests"
else
  ok ".env already present (not overwriting)"
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
  PG_HOST_PORT="$(cat .data/postgres-port 2>/dev/null || echo 5432)"
  ok "Postgres at localhost:$PG_HOST_PORT"

  step "Starting FalkorDB"
  scripts/start_falkordb.sh
  FALKOR_HOST_PORT="$(cat .data/falkordb-port 2>/dev/null || echo 6380)"
  ok "FalkorDB at localhost:$FALKOR_HOST_PORT"

  step "Running migrations"
  export POSTGRES_USER="${POSTGRES_USER:-memory}"
  export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-memory}"
  export POSTGRES_DB="${POSTGRES_DB:-memory_service}"
  export DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:${PG_HOST_PORT}/${POSTGRES_DB}}"
  uv run "${UV_FLAGS[@]}" alembic upgrade head
  ok "schema up to date"

  # Patch .env so uvicorn picks the actually-chosen ports next boot.
  # We only touch lines that still hold the default values — never
  # overwrite an operator's custom setting.
  if [[ -f .env ]]; then
    if [[ "$PG_HOST_PORT" != "5432" ]] && grep -q "^DATABASE_URL=postgresql.*localhost:5432/" .env; then
      sed -i.bak -E "s#(^DATABASE_URL=postgresql.*localhost:)5432(/.*)#\\1${PG_HOST_PORT}\\2#" .env
      rm -f .env.bak
      ok ".env DATABASE_URL updated to port $PG_HOST_PORT"
    fi
    if [[ "$FALKOR_HOST_PORT" != "6380" ]] && grep -q "^FALKORDB_PORT=6380$" .env; then
      sed -i.bak -E "s/^FALKORDB_PORT=6380$/FALKORDB_PORT=${FALKOR_HOST_PORT}/" .env
      rm -f .env.bak
      ok ".env FALKORDB_PORT updated to $FALKOR_HOST_PORT"
    fi
  fi
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
