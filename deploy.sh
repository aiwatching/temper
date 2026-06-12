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
#   ./deploy.sh restart      recreate memory-service container with current .env
#                            (picks up any .env changes — see note below)
#   ./deploy.sh stop         docker compose down (keeps volumes)
#   ./deploy.sh reset        docker compose down -v  (WIPES DATA)
#   ./deploy.sh logs         docker compose logs -f memory-service
#   ./deploy.sh status       container + health summary
#   ./deploy.sh update       git pull + rebuild image + restart
#   ./deploy.sh backup       snapshot postgres + falkordb to .data/backups/
#   ./deploy.sh restore <ts> restore a snapshot (overwrites live data)
#   ./deploy.sh install-backup-timer [--time HH:MM]
#                            systemd timer: run backup daily (default 03:00).
#                            NOTE: `up` already installs this automatically;
#                            use this only to change the time. Opt out with
#                            TEMPER_AUTO_BACKUP_TIMER=0; change default time
#                            with TEMPER_BACKUP_TIME=HH:MM.
#   ./deploy.sh backup-status   show next/last run + existing snapshots
#   ./deploy.sh fix-dns      Linux-only: configure docker daemon to use
#                            the host's upstream DNS resolvers. Run if
#                            containers can't reach internal corp
#                            domains that the host resolves fine
#                            (typical with systemd-resolved hosts).
#
# Note on `restart` vs plain `docker compose restart`:
#   compose's built-in `restart` only restarts PID 1 inside the
#   existing container — it does NOT re-read .env (the container's
#   env was frozen at create-time). `./deploy.sh restart` instead
#   does `up -d --force-recreate --no-deps --no-build memory-service`,
#   which destroys + recreates the container with current .env values.
#   So editing .env then `./deploy.sh restart` does what you want.

set -euo pipefail
cd "$(dirname "$0")"
SCRIPT_DIR="$(pwd)"

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

  # Passive DNS warning. Detect-only — fixing it (restarting docker)
  # is intrusive, so we point at the opt-in command instead of doing
  # it silently.
  if dns_needs_fix; then
    warn "host uses systemd-resolved + docker daemon has no DNS override."
    warn "Containers may fail to resolve internal corp domains."
    warn "Run './deploy.sh fix-dns' to auto-configure docker to use the host's upstream DNS."
    echo
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

  # Make sure daily backups are scheduled — automatically, so the
  # operator never has to remember `install-backup-timer`. Idempotent +
  # best-effort; won't fail the deploy.
  _ensure_backup_timer

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
  # --force-recreate destroys the container and creates a new one,
  # which means the new container reads the CURRENT .env. (Plain
  # `compose restart` only kicks PID 1 inside the existing container
  # — env was frozen at create-time.) --no-deps skips bouncing
  # postgres / falkordb / ollama just to apply a memory-service env
  # change. --no-build skips the image rebuild — that's what
  # `./deploy.sh update` is for after `git pull`.
  ok "recreating memory-service container with current .env"
  "${COMPOSE[@]}" up -d --force-recreate --no-deps --no-build memory-service
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

# Snapshot both stateful stores into .data/backups/<timestamp>/.
# Postgres: pg_dump custom format (compressed, pg_restore-friendly).
# FalkorDB: BGSAVE then copy the on-disk RDB out of the container.
# Credentials come from the postgres container's own env so we don't
# parse .env here.
cmd_backup() {
  require_docker
  # Timestamp from the running container's clock — avoids a host-side
  # `date` call that the sandbox may forbid, and keeps it monotonic
  # with the data it's snapshotting.
  local stamp
  stamp=$("${COMPOSE[@]}" exec -T postgres date -u +%Y%m%d-%H%M%S 2>/dev/null | tr -d '\r')
  [[ -n "$stamp" ]] || die "couldn't reach the postgres container — is the stack up? (./deploy.sh status)"
  local dir="$SCRIPT_DIR/.data/backups/$stamp"
  mkdir -p "$dir"

  ok "backing up postgres → $dir/postgres.dump"
  if ! "${COMPOSE[@]}" exec -T postgres sh -c \
      'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > "$dir/postgres.dump" 2>"$dir/postgres.err"; then
    cat "$dir/postgres.err" >&2
    die "pg_dump failed — see $dir/postgres.err"
  fi
  rm -f "$dir/postgres.err"

  ok "backing up falkordb (BGSAVE + copy RDB)"
  "${COMPOSE[@]}" exec -T falkordb redis-cli BGSAVE >/dev/null 2>&1 || true
  # Wait for the background save to finish (rdb_bgsave_in_progress:0).
  local i=0
  while (( i < 30 )); do
    if "${COMPOSE[@]}" exec -T falkordb redis-cli INFO persistence 2>/dev/null \
        | tr -d '\r' | grep -q '^rdb_bgsave_in_progress:0'; then
      break
    fi
    sleep 1; i=$((i + 1))
  done
  # Resolve the RDB path from the server config; defaults vary by image.
  local rdb_dir rdb_file
  rdb_dir=$("${COMPOSE[@]}" exec -T falkordb redis-cli CONFIG GET dir 2>/dev/null | tr -d '\r' | sed -n '2p')
  rdb_file=$("${COMPOSE[@]}" exec -T falkordb redis-cli CONFIG GET dbfilename 2>/dev/null | tr -d '\r' | sed -n '2p')
  rdb_dir="${rdb_dir:-/var/lib/falkordb/data}"
  rdb_file="${rdb_file:-dump.rdb}"
  if "${COMPOSE[@]}" cp "falkordb:${rdb_dir%/}/$rdb_file" "$dir/falkordb.rdb" 2>/dev/null; then
    ok "falkordb RDB saved"
  else
    warn "couldn't copy the FalkorDB RDB from ${rdb_dir%/}/$rdb_file — postgres dump is still good"
  fi

  # Keep the last 20 backups; prune older. (while-loop instead of
  # `xargs -r` for macOS portability.)
  ls -1dt "$SCRIPT_DIR/.data/backups"/*/ 2>/dev/null | tail -n +21 | while read -r old; do
    rm -rf "$old"
  done

  echo
  ok "backup complete: $dir"
  echo "  postgres:  $(du -h "$dir/postgres.dump" 2>/dev/null | cut -f1)"
  [[ -f "$dir/falkordb.rdb" ]] && echo "  falkordb:  $(du -h "$dir/falkordb.rdb" | cut -f1)"
  echo "  restore:   ./deploy.sh restore $stamp"
}

cmd_restore() {
  require_docker
  local stamp="${1:-}"
  if [[ -z "$stamp" ]]; then
    echo "Available backups:"
    ls -1dt "$SCRIPT_DIR/.data/backups"/*/ 2>/dev/null | sed 's#.*/backups/##; s#/$##' | sed 's/^/  /'
    die "usage: ./deploy.sh restore <timestamp>"
  fi
  local dir="$SCRIPT_DIR/.data/backups/$stamp"
  [[ -d "$dir" ]] || die "no backup at $dir"
  [[ -f "$dir/postgres.dump" ]] || die "missing $dir/postgres.dump"

  warn "RESTORE OVERWRITES the live database with the $stamp snapshot."
  warn "Current postgres data will be REPLACED. FalkorDB is restored only"
  warn "if a falkordb.rdb is present in the backup."
  printf "  type the timestamp '%s' to confirm: " "$stamp"
  read -r confirm
  [[ "$confirm" == "$stamp" ]] || die "aborted"

  ok "restoring postgres (drop + recreate objects via pg_restore --clean)"
  if ! "${COMPOSE[@]}" exec -T postgres sh -c \
      'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner' \
      < "$dir/postgres.dump"; then
    warn "pg_restore reported errors (often harmless --clean drops on first restore)"
  fi

  if [[ -f "$dir/falkordb.rdb" ]]; then
    ok "restoring falkordb RDB (requires a falkordb restart to load)"
    local rdb_dir rdb_file
    rdb_dir=$("${COMPOSE[@]}" exec -T falkordb redis-cli CONFIG GET dir 2>/dev/null | tr -d '\r' | sed -n '2p')
    rdb_file=$("${COMPOSE[@]}" exec -T falkordb redis-cli CONFIG GET dbfilename 2>/dev/null | tr -d '\r' | sed -n '2p')
    rdb_dir="${rdb_dir:-/var/lib/falkordb/data}"; rdb_file="${rdb_file:-dump.rdb}"
    "${COMPOSE[@]}" cp "$dir/falkordb.rdb" "falkordb:${rdb_dir%/}/$rdb_file" \
      && "${COMPOSE[@]}" restart falkordb \
      && ok "falkordb restarted with restored RDB" \
      || warn "falkordb RDB restore failed — restore it manually"
  fi

  ok "restore done. Bounce the service: ./deploy.sh restart"
}

# Install a systemd timer that runs `./deploy.sh backup` daily.
# systemd over cron: survives reboot, journald logs, `list-timers`
# shows next run, Persistent=true catches up a run missed while the
# box was off.
# True if the temper-backup timer unit is already installed.
_backup_timer_installed() {
  command -v systemctl >/dev/null 2>&1 || return 1
  systemctl list-unit-files temper-backup.timer >/dev/null 2>&1 \
    && systemctl cat temper-backup.timer >/dev/null 2>&1
}

# Write the unit files + enable the timer. `sudo_cmd` is the sudo
# invocation to use ("sudo" or "sudo -n" for non-interactive).
_write_backup_timer() {
  local at="$1" sudo_cmd="$2"
  local user; user="$(id -un)"
  $sudo_cmd tee /etc/systemd/system/temper-backup.service >/dev/null <<EOF
[Unit]
Description=TEMPER daily backup (pg_dump + falkordb RDB)
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
User=$user
WorkingDirectory=$SCRIPT_DIR
ExecStart=$SCRIPT_DIR/deploy.sh backup
EOF
  $sudo_cmd tee /etc/systemd/system/temper-backup.timer >/dev/null <<EOF
[Unit]
Description=Run TEMPER backup daily

[Timer]
OnCalendar=*-*-* $at
Persistent=true

[Install]
WantedBy=timers.target
EOF
  $sudo_cmd systemctl daemon-reload \
    && $sudo_cmd systemctl enable --now temper-backup.timer
}

cmd_install_backup_timer() {
  if [[ "$(uname)" != "Linux" ]]; then
    die "install-backup-timer is Linux/systemd only. On macOS run ./deploy.sh backup from a launchd job or cron yourself."
  fi
  command -v systemctl >/dev/null 2>&1 || die "systemctl not found — this host isn't systemd-managed."

  local at="03:00"
  if [[ "${1:-}" == "--time" && -n "${2:-}" ]]; then
    at="$2"
  fi
  # OnCalendar wants HH:MM:SS; accept HH:MM and append seconds.
  [[ "$at" =~ ^[0-9]{2}:[0-9]{2}$ ]] && at="$at:00"

  ok "installing backup timer (daily at $at)"
  _write_backup_timer "$at" "sudo"
  echo
  ok "backup timer installed + enabled."
  echo "  Next runs:   ./deploy.sh backup-status"
  echo "  Run now:     sudo systemctl start temper-backup.service"
  echo "  Logs:        journalctl -u temper-backup.service"
  echo "  Remove:      ./deploy.sh uninstall-backup-timer"
}

# Called from cmd_up so the daily backup timer is set up automatically
# on deploy — the operator never has to run install-backup-timer by
# hand. Best-effort + idempotent: silent if already installed, skipped
# on non-systemd / opt-out, and NEVER fails the deploy. Needs root to
# write the unit files; uses passwordless sudo if available, otherwise
# prints a one-line hint instead of blocking the deploy on a password.
_ensure_backup_timer() {
  # Every early-out uses explicit `if`: this runs inside cmd_up under
  # `set -e`, and a bare `cmd && return` whose left side "fails" can
  # abort the whole deploy. Never let backup setup do that.
  if [[ "$(uname)" != "Linux" ]]; then return 0; fi
  if ! command -v systemctl >/dev/null 2>&1; then return 0; fi
  # Opt out: TEMPER_AUTO_BACKUP_TIMER=0 (e.g. you run your own backups).
  if [[ "${TEMPER_AUTO_BACKUP_TIMER:-1}" == "0" ]]; then return 0; fi
  if _backup_timer_installed; then return 0; fi   # already there

  local at="${TEMPER_BACKUP_TIME:-03:00}"
  [[ "$at" =~ ^[0-9]{2}:[0-9]{2}$ ]] && at="$at:00"

  if sudo -n true 2>/dev/null; then
    # Passwordless sudo — install quietly.
    if _write_backup_timer "$at" "sudo -n" >/dev/null 2>&1; then
      ok "daily backup timer installed (runs $at; ./deploy.sh backup-status)"
    else
      warn "couldn't auto-install the backup timer — run: ./deploy.sh install-backup-timer"
    fi
  elif [[ -t 0 ]]; then
    # Interactive terminal — one-time sudo prompt is acceptable here.
    info "setting up the daily backup timer (one-time sudo) ..."
    if _write_backup_timer "$at" "sudo"; then
      ok "daily backup timer installed (runs $at)"
    else
      warn "backup timer not installed — run: ./deploy.sh install-backup-timer"
    fi
  else
    warn "daily backups not scheduled (needs sudo). Run once: ./deploy.sh install-backup-timer"
  fi
}

cmd_backup_status() {
  command -v systemctl >/dev/null 2>&1 || die "systemctl not found."
  if ! systemctl list-unit-files temper-backup.timer >/dev/null 2>&1 \
      || ! systemctl cat temper-backup.timer >/dev/null 2>&1; then
    warn "no backup timer installed. Run ./deploy.sh install-backup-timer"
    return 1
  fi
  systemctl list-timers temper-backup.timer --no-pager 2>/dev/null || true
  echo
  echo "Last run:"
  systemctl status temper-backup.service --no-pager -n 5 2>/dev/null \
    | sed -n '1,8p' || true
  echo
  echo "Existing snapshots in .data/backups:"
  ls -1dt "$SCRIPT_DIR/.data/backups"/*/ 2>/dev/null | sed 's#.*/backups/##; s#/$##' | sed 's/^/  /' | head -10
}

cmd_uninstall_backup_timer() {
  command -v systemctl >/dev/null 2>&1 || die "systemctl not found."
  sudo systemctl disable --now temper-backup.timer 2>/dev/null || true
  sudo rm -f /etc/systemd/system/temper-backup.timer /etc/systemd/system/temper-backup.service
  sudo systemctl daemon-reload
  ok "backup timer removed. Existing snapshots in .data/backups are kept."
}

# Detect: host uses systemd-resolved (resolv.conf points at 127.0.0.53)
# AND docker daemon.json doesn't yet have a `dns` override.
#
# This is the combo that produces "Name or service not known" inside
# containers when the host can resolve internal corp domains fine —
# docker sees 127.0.0.53 in /etc/resolv.conf, decides the loopback stub
# can't possibly be reached from inside a container (it can't), and
# silently substitutes 8.8.8.8. Public DNS → can't resolve `*.corp.internal`.
#
# Returns 0 (true) if the issue is likely present. Linux-only check;
# Docker Desktop on Mac/Windows handles DNS via its own VM and doesn't
# hit this.
dns_needs_fix() {
  [[ "$(uname)" == "Linux" ]] || return 1
  [[ -f /etc/resolv.conf ]] || return 1
  grep -qE '^\s*nameserver\s+127\.0\.0\.53' /etc/resolv.conf || return 1
  # If daemon.json already sets `dns`, assume the operator knows what
  # they're doing — don't pester.
  if [[ -f /etc/docker/daemon.json ]] && grep -q '"dns"' /etc/docker/daemon.json; then
    return 1
  fi
  return 0
}

cmd_fix_dns() {
  require_docker

  if [[ "$(uname)" != "Linux" ]]; then
    die "fix-dns is Linux-only. Docker Desktop (Mac/Windows) handles container DNS through its VM and doesn't hit this issue."
  fi

  # Pull the real upstream resolvers — what systemd-resolved is
  # actually forwarding to. resolvectl is the canonical source.
  # /run/systemd/resolve/resolv.conf is the same data in resolv.conf
  # shape and is the fallback.
  local upstream=""
  if command -v resolvectl >/dev/null 2>&1; then
    upstream=$(resolvectl status 2>/dev/null \
      | awk -F: '/^[[:space:]]+DNS Servers:/ {print $2; exit}' \
      | xargs -n1 2>/dev/null | grep -E '^[0-9a-f.:]+$' | tr '\n' ' ')
  fi
  if [[ -z "$upstream" ]] && [[ -f /run/systemd/resolve/resolv.conf ]]; then
    upstream=$(awk '/^nameserver/ {print $2}' /run/systemd/resolve/resolv.conf | tr '\n' ' ')
  fi
  upstream=$(echo "$upstream" | xargs)   # trim

  if [[ -z "$upstream" ]]; then
    die "couldn't auto-detect upstream DNS. Find it with 'resolvectl status' and edit /etc/docker/daemon.json manually."
  fi

  info "detected upstream DNS: $upstream"

  # Build the daemon.json content. If a file already exists, merge our
  # dns key in via python so we don't trash unrelated settings (e.g.
  # registry mirrors, log opts, etc.). Public 8.8.8.8 is appended so
  # `docker pull <public-image>` still works when behind the internal
  # resolver.
  local new_json
  if command -v python3 >/dev/null 2>&1; then
    new_json=$(python3 - "$upstream" <<'PY'
import json, os, sys
upstream = sys.argv[1].split()
path = "/etc/docker/daemon.json"
cfg = {}
if os.path.exists(path):
    try:
        with open(path) as f: cfg = json.load(f)
    except Exception:
        cfg = {}
dns = upstream + ["8.8.8.8"]
seen = set(); cfg["dns"] = [x for x in dns if not (x in seen or seen.add(x))]
print(json.dumps(cfg, indent=2))
PY
)
  else
    # Fallback for python-less systems: only write if file is absent;
    # refuse to silently nuke an existing config we can't parse.
    if [[ -f /etc/docker/daemon.json ]]; then
      die "/etc/docker/daemon.json already exists and python3 is missing. Either install python3 or merge the 'dns' key by hand."
    fi
    local dns_csv
    dns_csv=$(printf '"%s",' $upstream)
    new_json=$(printf '{\n  "dns": [%s"8.8.8.8"]\n}\n' "$dns_csv")
  fi

  echo
  echo "  Will write /etc/docker/daemon.json:"
  echo "$new_json" | sed 's/^/    /'
  echo
  warn "Applying this will restart Docker — ALL containers on this host"
  warn "(not just TEMPER's) will see a brief downtime while they recreate."
  printf "  Proceed? (type 'yes' to continue): "
  read -r confirm
  [[ "$confirm" == "yes" ]] || die "aborted"

  # Backup the old config so reverting is just `mv .bak daemon.json + restart docker`.
  if [[ -f /etc/docker/daemon.json ]]; then
    local stamp; stamp=$(date +%Y%m%d-%H%M%S)
    sudo cp /etc/docker/daemon.json "/etc/docker/daemon.json.bak.${stamp}"
    ok "backed up old config to /etc/docker/daemon.json.bak.${stamp}"
  fi

  sudo mkdir -p /etc/docker
  echo "$new_json" | sudo tee /etc/docker/daemon.json >/dev/null
  ok "wrote /etc/docker/daemon.json"

  ok "restarting docker daemon"
  sudo systemctl restart docker
  sleep 3

  ok "bringing TEMPER stack back up"
  "${COMPOSE[@]}" up -d

  echo
  ok "fix-dns done."
  echo "  Verify with:"
  echo "    curl -s 'http://localhost:${MS_PORT:-18088}/v1/health?deep=true' | python3 -m json.tool"
  echo "  The llm/embedder blocks should show 'ok: true' with real probe details."
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
  backup)       cmd_backup ;;
  restore)      cmd_restore "${1:-}" ;;
  install-backup-timer)   cmd_install_backup_timer "${1:-}" "${2:-}" ;;
  backup-status)          cmd_backup_status ;;
  uninstall-backup-timer) cmd_uninstall_backup_timer ;;
  fix-dns)      cmd_fix_dns ;;
  -h|--help|help)
    sed -n '2,31p' "$0" | sed 's/^# \{0,1\}//'
    ;;
  *)
    die "unknown subcommand: $sub  (try: up / restart / stop / reset / logs / status / update / backup / restore / install-backup-timer / backup-status / fix-dns)"
    ;;
esac
