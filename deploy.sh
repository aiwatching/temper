#!/usr/bin/env bash
# deploy.sh — one-command docker-only deploy of TEMPER.
#
# Use for: fresh server, no databases pre-installed, you just want
# "git clone + ./deploy.sh + go". Postgres + FalkorDB + memory-service
# all run as containers; nothing native to install.
#
# Difference from install.sh:
#   install.sh — local dev. Installs uv + Python, supports --no-docker,
#                does port auto-detect, runs alembic on the host.
#   deploy.sh  — pure docker. ONLY needs docker. Migrations run
#                inside the memory-service container's entrypoint.
#                Binds MS_BIND=0.0.0.0 so the host's external IP works.
#
# Usage:
#   ./deploy.sh              up (first run does cp .env.example + edit prompt)
#   ./deploy.sh restart      docker compose restart memory-service
#   ./deploy.sh stop         docker compose down (keeps volumes)
#   ./deploy.sh reset        docker compose down -v  (WIPES DATA)
#   ./deploy.sh logs         docker compose logs -f memory-service
#   ./deploy.sh status       container + health summary
#   ./deploy.sh update       git pull + rebuild + restart

set -euo pipefail
cd "$(dirname "$0")"

if [[ -t 1 ]]; then
  C_GREEN=$'\e[32m' C_YELLOW=$'\e[33m' C_RED=$'\e[31m' C_BOLD=$'\e[1m' C_DIM=$'\e[2m' C_RST=$'\e[0m'
else
  C_GREEN='' C_YELLOW='' C_RED='' C_BOLD='' C_DIM='' C_RST=''
fi
ok()   { printf '  %s✓%s %s\n' "$C_GREEN" "$C_RST" "$*"; }
warn() { printf '  %s⚠%s %s\n' "$C_YELLOW" "$C_RST" "$*"; }
info() { printf '  %s•%s %s\n' "$C_DIM" "$C_RST" "$*"; }
die()  { printf '  %s✗%s %s\n' "$C_RED" "$C_RST" "$*" >&2; exit 1; }

require_docker() {
  command -v docker >/dev/null 2>&1 || die "docker not found. Install Docker first: https://docs.docker.com/engine/install/"
  docker info >/dev/null 2>&1 || die "docker daemon not reachable. Is the Docker service running? (sudo systemctl start docker / open Docker Desktop)"
  if docker compose version >/dev/null 2>&1; then
    COMPOSE_BASE=(docker compose)
  elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_BASE=(docker-compose)
  else
    die "docker compose v2 not available. Upgrade Docker Engine (>= 24) or install the compose plugin."
  fi

  # GPU overlay: include the nvidia override iff the host has
  # nvidia-smi. The overlay is a no-op without nvidia-container-toolkit
  # but compose will then refuse to schedule the device reservation —
  # so only opt in when we know it'll work.
  COMPOSE=("${COMPOSE_BASE[@]}" -f docker-compose.yml)
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    COMPOSE+=(-f docker-compose.gpu.yml)
    GPU_DETECTED=1
  else
    GPU_DETECTED=0
  fi
}

# Best-effort host IP for the "open this URL" hint.
# Order: external default route → first non-loopback IPv4 → localhost.
detect_host_ip() {
  local ip=""
  if command -v ip >/dev/null 2>&1; then
    ip=$(ip route get 1.1.1.1 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i=="src") {print $(i+1); exit}}')
  fi
  if [[ -z "$ip" ]] && command -v hostname >/dev/null 2>&1; then
    ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  fi
  if [[ -z "$ip" ]] && command -v ifconfig >/dev/null 2>&1; then
    ip=$(ifconfig 2>/dev/null | awk '/inet / && $2 != "127.0.0.1" {print $2; exit}')
  fi
  echo "${ip:-localhost}"
}

# Pick a strong SECRET_KEY without requiring python on the host —
# openssl is universally available wherever docker is.
gen_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 48 | tr -d '\n='
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
  else
    # Fall back to /dev/urandom + base64 in coreutils.
    head -c 48 /dev/urandom | base64 | tr -d '\n='
  fi
}

# First-boot env bootstrap. Copies .env.example → .env and patches
# the four things that MUST differ from the dev template:
#   SECRET_KEY              (must be unique per deploy)
#   APP_ENV                 (production-friendly defaults flip)
#   MS_BIND                 (0.0.0.0 so the host's IP actually serves)
#   FALKORDB_PORT           (6379 — that's what the falkordb container
#                            speaks on the compose network)
#   ALLOW_SELF_REGISTRATION (default-closed for remote deploys)
#
# Doesn't touch LLM_* / EMBEDDING_* — those need real keys and we
# stop after bootstrap so the user can paste them in.
bootstrap_env() {
  cp .env.example .env

  local secret
  secret=$(gen_secret)

  # macOS sed and GNU sed disagree on -i; use the BSD-portable
  # `-i.bak` + rm pattern.
  #
  # Embedding bootstrap: point at the bundled ollama service. The
  # ollama-pull sidecar auto-downloads bge-m3 on first `up`, so this
  # works without any LLM provider config.
  sed -i.bak \
    -e "s|^SECRET_KEY=.*|SECRET_KEY=${secret}|" \
    -e "s|^APP_ENV=.*|APP_ENV=production|" \
    -e "s|^LOG_FORMAT=.*|LOG_FORMAT=json|" \
    -e "s|^MS_BIND=.*|MS_BIND=0.0.0.0|" \
    -e "s|^FALKORDB_PORT=.*|FALKORDB_PORT=6379|" \
    -e "s|^ALLOW_SELF_REGISTRATION=.*|ALLOW_SELF_REGISTRATION=false|" \
    -e "s|^EMBEDDING_PROVIDER=.*|EMBEDDING_PROVIDER=ollama|" \
    -e "s|^EMBEDDING_BASE_URL=.*|EMBEDDING_BASE_URL=http://ollama:11434/v1|" \
    -e "s|^EMBEDDING_MODEL=.*|EMBEDDING_MODEL=bge-m3|" \
    -e "s|^EMBEDDING_DIMENSIONS=.*|EMBEDDING_DIMENSIONS=1024|" \
    .env
  rm -f .env.bak
}

cmd_up() {
  require_docker

  if [[ ! -f .env ]]; then
    ok "first run — bootstrapping .env from .env.example"
    bootstrap_env
    info "generated SECRET_KEY, set APP_ENV=production, MS_BIND=0.0.0.0"
    info "embedding pre-wired to bundled ollama service (bge-m3, 1024-dim)"
    if (( GPU_DETECTED )); then
      info "nvidia GPU detected — docker-compose.gpu.yml will be included"
    else
      warn "no nvidia-smi on host — ollama will run on CPU (bge-m3 ~200-400ms/req)"
    fi
    echo
    warn "Edit .env to fill in your LLM key, then re-run ./deploy.sh:"
    echo "    ${C_BOLD}vi .env${C_RST}"
    echo
    echo "  Required:"
    echo "    LLM_PROVIDER, LLM_API_KEY (+ BASE_URL/MODEL if non-openai)"
    echo "    POSTGRES_PASSWORD  (set to a strong value — postgres volume is created with this)"
    echo
    echo "  Recommended:"
    echo "    DEFAULT_ADMIN_PASSWORD  (default 'admin' — CHANGE for any reachable deploy)"
    echo "    CORS_ALLOW_ORIGINS      (your frontend origin if any)"
    echo
    echo "  Embedding is bundled — no API key needed. To use a different model:"
    echo "    EMBEDDING_MODEL=...     (also adjust EMBEDDING_DIMENSIONS to match)"
    return 0
  fi

  # Sanity-check: catch the obvious "user forgot to edit" cases before
  # docker eats 30 seconds building images that will then crash.
  if grep -qE '^SECRET_KEY=change-me' .env; then
    die ".env still has the placeholder SECRET_KEY. Edit it first."
  fi
  if grep -qE '^LLM_API_KEY=$' .env && ! grep -qE '^LLM_PROVIDER=ollama' .env; then
    warn "LLM_API_KEY is empty. graphiti extraction will fail on every write until you set it."
  fi

  ok "building + starting docker stack"
  "${COMPOSE[@]}" up -d --build

  echo
  ok "waiting for memory-service health..."
  local attempts=0
  local healthy=0
  while (( attempts < 60 )); do
    if "${COMPOSE[@]}" ps memory-service --format '{{.Status}}' 2>/dev/null | grep -q 'healthy'; then
      healthy=1
      break
    fi
    sleep 2
    attempts=$((attempts + 1))
  done

  if (( healthy )); then
    ok "memory-service is healthy"
  else
    warn "memory-service did not become healthy within 120s"
    warn "check logs: ./deploy.sh logs"
  fi

  local host_ip port bind
  host_ip=$(detect_host_ip)
  port=$(grep -E '^MS_PORT=' .env | tail -1 | cut -d= -f2)
  port="${port:-18088}"
  bind=$(grep -E '^MS_BIND=' .env | tail -1 | cut -d= -f2)

  echo
  ok "TEMPER is up"
  echo
  if [[ "$bind" == "0.0.0.0" ]]; then
    echo "  Admin UI:  http://${host_ip}:${port}/admin"
    echo "  API docs:  http://${host_ip}:${port}/docs"
  else
    echo "  Admin UI:  http://${bind}:${port}/admin  ${C_DIM}(local only — MS_BIND=${bind})${C_RST}"
  fi
  echo
  echo "  Default admin (first boot): admin@example.com / admin"
  echo "  ${C_YELLOW}Change it immediately via /admin/me${C_RST}"
  echo
  echo "  Logs:    ./deploy.sh logs"
  echo "  Stop:    ./deploy.sh stop"
  echo "  Update:  ./deploy.sh update"
}

cmd_restart() {
  require_docker
  ok "restarting memory-service"
  "${COMPOSE[@]}" restart memory-service
}

cmd_stop() {
  require_docker
  ok "stopping stack (volumes preserved)"
  "${COMPOSE[@]}" down
}

cmd_reset() {
  require_docker
  warn "this will DESTROY all data in postgres + falkordb volumes."
  read -r -p "  type 'reset' to confirm: " confirm
  [[ "$confirm" == "reset" ]] || die "aborted"
  "${COMPOSE[@]}" down -v
  ok "wiped. Run ./deploy.sh to start fresh."
}

cmd_logs() {
  require_docker
  "${COMPOSE[@]}" logs -f --tail=200 memory-service
}

cmd_status() {
  require_docker
  "${COMPOSE[@]}" ps
}

cmd_update() {
  require_docker
  ok "git pull --ff-only"
  git pull --ff-only
  ok "rebuilding memory-service"
  "${COMPOSE[@]}" build memory-service
  ok "restarting"
  "${COMPOSE[@]}" up -d memory-service
}

sub="${1:-up}"
shift || true
case "$sub" in
  up|start|"")  cmd_up ;;
  restart)      cmd_restart ;;
  stop|down)    cmd_stop ;;
  reset)        cmd_reset ;;
  logs)         cmd_logs ;;
  status|ps)    cmd_status ;;
  update)       cmd_update ;;
  -h|--help|help)
    sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'
    ;;
  *)
    die "unknown subcommand: $sub  (try: up / restart / stop / reset / logs / status / update)"
    ;;
esac
