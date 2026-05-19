#!/usr/bin/env bash
# start.sh — TEMPER lifecycle control.
#
# Subcommands:
#   start [--fg]         start the service (default: background)
#   stop                 SIGTERM the running process
#   restart [--fg]       stop + start
#   status               is it running? PID + uptime
#   logs [-n N] [--no-follow]
#                        tail (and follow) the log file
#
# With no arguments: `start` (background). The legacy `scripts/dev.sh`
# is now a thin wrapper that calls `start.sh start --fg`.
#
# Assumes `./install.sh` has been run at least once. start.sh is
# fast-path only — it does NOT install deps, run migrations, or boot
# Postgres. Use install.sh for that, then start.sh to control the
# process.
#
# Layout:
#   .data/temper.pid              running process PID
#   .data/logs/temper.log         stdout + stderr (rotated at 50MB)
#   .data/logs/temper.log.{1..5}  rotated history (oldest dropped)
#
# Override defaults via env:
#   MS_PORT=8080 ./start.sh       custom port
#   MS_HOST=0.0.0.0 ./start.sh    bind all interfaces (auth required!)

set -euo pipefail
cd "$(dirname "$0")"

# ---- paths -----------------------------------------------------
DATA_DIR=".data"
LOG_DIR="$DATA_DIR/logs"
LOG_FILE="$LOG_DIR/temper.log"
PID_FILE="$DATA_DIR/temper.pid"
LOG_MAX_BYTES=52428800   # 50 MB
LOG_KEEP=5               # .1 .. .5

mkdir -p "$LOG_DIR"

# ---- helpers ---------------------------------------------------
HOST="${MS_HOST:-127.0.0.1}"
PORT="${MS_PORT:-18088}"

if [[ -t 1 ]]; then
  C_GREEN=$'\e[32m' C_YELLOW=$'\e[33m' C_RED=$'\e[31m' C_DIM=$'\e[2m' C_RST=$'\e[0m'
else
  C_GREEN='' C_YELLOW='' C_RED='' C_DIM='' C_RST=''
fi
ok()   { printf '  %s✓%s %s\n' "$C_GREEN" "$C_RST" "$*"; }
warn() { printf '  %s⚠%s %s\n' "$C_YELLOW" "$C_RST" "$*"; }
die()  { printf '  %s✗%s %s\n' "$C_RED" "$C_RST" "$*" >&2; exit 1; }

# Resolve uv even when ~/.local/bin isn't on PATH for this shell.
if ! command -v uv >/dev/null 2>&1; then
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
command -v uv >/dev/null 2>&1 || die "uv not found. Run ./install.sh first."

# Build DATABASE_URL the same way install.sh did. Honor the host
# port that start_postgres.sh wrote to .data/postgres-port — that's
# where the dev Postgres actually ended up after possible
# port-collision auto-bump.
PG_USER="${POSTGRES_USER:-memory}"
PG_PASS="${POSTGRES_PASSWORD:-memory}"
PG_DB="${POSTGRES_DB:-memory_service}"
PG_HOST_PORT="$(cat .data/postgres-port 2>/dev/null || echo 5432)"
DEFAULT_DB="postgresql+asyncpg://${PG_USER}:${PG_PASS}@localhost:${PG_HOST_PORT}/${PG_DB}"
export DATABASE_URL="${DATABASE_URL:-$DEFAULT_DB}"

# ---- log rotation ---------------------------------------------
#
# Called once before each foreground/background start. If the log
# is already over LOG_MAX_BYTES, shift .1..N down by one (dropping
# the oldest) and start fresh. Cheap, no external deps (no
# logrotate dependency).
rotate_log() {
  [[ -f "$LOG_FILE" ]] || return 0
  # Portable file-size probe — BSD stat (macOS) and GNU stat (Linux)
  # disagree on flags; try GNU first, fall back to BSD.
  local size
  size=$(stat -c%s "$LOG_FILE" 2>/dev/null || stat -f%z "$LOG_FILE" 2>/dev/null || echo 0)
  if (( size < LOG_MAX_BYTES )); then return 0; fi

  ok "rotating log ($((size / 1024 / 1024)) MB ≥ 50 MB threshold)"
  # Shift .keep-1 → .keep, ..., .1 → .2, current → .1
  rm -f "${LOG_FILE}.${LOG_KEEP}"
  local i
  for (( i = LOG_KEEP - 1; i >= 1; i-- )); do
    [[ -f "${LOG_FILE}.${i}" ]] && mv "${LOG_FILE}.${i}" "${LOG_FILE}.$((i + 1))"
  done
  mv "$LOG_FILE" "${LOG_FILE}.1"
  : > "$LOG_FILE"   # truncate fresh
}

# ---- process state --------------------------------------------
is_running() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid=$(cat "$PID_FILE" 2>/dev/null || echo "")
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

read_pid() {
  [[ -f "$PID_FILE" ]] && cat "$PID_FILE"
}

# ---- the actual uvicorn launch --------------------------------
# Shared by both --fg and background modes. Returns the uvicorn
# command as an array via stdout-via-eval pattern is ugly; use a
# function that exec's.
run_uvicorn_exec() {
  # --reload-dir limits the watcher to TEMPER's own source; without
  # it uvicorn watches cwd (which includes agents/smith/) and Smith
  # edits would restart TEMPER.
  exec uv run uvicorn memory_service.main:app --reload \
    --reload-dir src/memory_service \
    --host "$HOST" --port "$PORT"
}

cmd_start() {
  local fg=0
  for a in "$@"; do
    case "$a" in
      --fg|--foreground) fg=1 ;;
      *) die "unknown start arg: $a" ;;
    esac
  done

  if is_running; then
    warn "already running (PID $(read_pid))"
    warn "use './start.sh restart' or './start.sh stop' first"
    return 0
  fi

  rotate_log

  if (( fg )); then
    ok "starting in foreground at http://$HOST:$PORT"
    echo "  log: $LOG_FILE  (Ctrl-C to stop)"
    # In foreground we just exec — no PID file (the user is the
    # supervisor). Output goes to terminal AND tee'd to log so
    # `./start.sh logs` still has something to show.
    run_uvicorn_exec 2>&1 | tee -a "$LOG_FILE"
    return
  fi

  # Background — nohup + & + PID file.
  ok "starting in background at http://$HOST:$PORT"
  # Use setsid to detach from the controlling terminal so closing
  # the shell doesn't HUP us. Fall back to nohup if setsid missing
  # (some minimal containers).
  if command -v setsid >/dev/null 2>&1; then
    setsid bash -c "exec uv run uvicorn memory_service.main:app --reload \
      --reload-dir src/memory_service \
      --host '$HOST' --port '$PORT' \
      >> '$LOG_FILE' 2>&1" </dev/null >/dev/null 2>&1 &
  else
    nohup bash -c "exec uv run uvicorn memory_service.main:app --reload \
      --reload-dir src/memory_service \
      --host '$HOST' --port '$PORT' \
      >> '$LOG_FILE' 2>&1" </dev/null >/dev/null 2>&1 &
  fi
  local launched_pid=$!
  echo "$launched_pid" > "$PID_FILE"

  # Wait briefly + verify it actually came up (catches "uv sync
  # failed mid-boot" / "port in use" / etc. and reports right away
  # instead of leaving a stale PID file).
  sleep 1
  if ! kill -0 "$launched_pid" 2>/dev/null; then
    rm -f "$PID_FILE"
    die "failed to start. Check log: tail -50 $LOG_FILE"
  fi

  ok "PID $launched_pid"
  echo
  echo "  Admin:    http://$HOST:$PORT/admin"
  echo "  Logs:     ./start.sh logs"
  echo "  Stop:     ./start.sh stop"
  echo "  Status:   ./start.sh status"
}

cmd_stop() {
  if ! is_running; then
    warn "not running (no PID file or process gone)"
    rm -f "$PID_FILE"
    return 0
  fi
  local pid
  pid=$(read_pid)
  ok "stopping PID $pid"
  kill "$pid"
  # Wait up to 10s for clean exit.
  local i=0
  while kill -0 "$pid" 2>/dev/null && (( i < 10 )); do
    sleep 1
    i=$((i + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    warn "didn't exit in 10s — sending SIGKILL"
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
  ok "stopped"
}

cmd_restart() {
  cmd_stop
  cmd_start "$@"
}

cmd_status() {
  if is_running; then
    local pid
    pid=$(read_pid)
    local started
    started=$(ps -o lstart= -p "$pid" 2>/dev/null | xargs)
    ok "running (PID $pid, started $started)"
    echo "  Endpoint: http://$HOST:$PORT"
    echo "  Log:      $LOG_FILE ($(stat -c%s "$LOG_FILE" 2>/dev/null || stat -f%z "$LOG_FILE" 2>/dev/null || echo 0) bytes)"
  else
    warn "not running"
    [[ -f "$LOG_FILE" ]] && echo "  Last log: $LOG_FILE"
    return 1
  fi
}

cmd_logs() {
  local n=200
  local follow=1
  while (( $# )); do
    case "$1" in
      -n) n="$2"; shift 2 ;;
      --no-follow) follow=0; shift ;;
      *) die "unknown logs arg: $1" ;;
    esac
  done
  if [[ ! -f "$LOG_FILE" ]]; then
    warn "no log file yet at $LOG_FILE"
    return 0
  fi
  if (( follow )); then
    tail -n "$n" -f "$LOG_FILE"
  else
    tail -n "$n" "$LOG_FILE"
  fi
}

# ---- dispatch -------------------------------------------------
sub="${1:-start}"
shift || true
case "$sub" in
  start)   cmd_start "$@" ;;
  stop)    cmd_stop ;;
  restart) cmd_restart "$@" ;;
  status)  cmd_status ;;
  logs)    cmd_logs "$@" ;;
  -h|--help|help)
    sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) die "unknown subcommand: $sub (try 'start' / 'stop' / 'restart' / 'status' / 'logs')" ;;
esac
