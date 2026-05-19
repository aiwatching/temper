#!/usr/bin/env bash
# scripts/start_falkordb.sh
#
# Idempotent FalkorDB bootstrap.
#
# Two host ports to expose:
#   PORT          RESP / GRAPH.QUERY (container 6379). Default 6380.
#   BROWSER_PORT  Browser UI (container 3000). Default 3000.
#                 Set to 0 to disable the browser publish entirely.
#
# Both auto-bump when their default is in use. Chosen RESP port goes
# to `.data/falkordb-port` so install.sh / start.sh build the right
# FALKORDB_PORT env at runtime. Chosen browser port goes to
# `.data/falkordb-browser-port`.
#
# Override with env:
#   FALKORDB_PORT=NNNN ./start_falkordb.sh         pin RESP port
#   FALKORDB_BROWSER_PORT=NNNN ./start_falkordb.sh pin browser port (0=off)

set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p .data
PORT_FILE=".data/falkordb-port"
BROWSER_FILE=".data/falkordb-browser-port"

CONTAINER_NAME="${FALKORDB_CONTAINER:-memory-service-falkordb}"
IMAGE="${FALKORDB_IMAGE:-falkordb/falkordb:latest}"
GRAPH="_healthcheck"   # matches adapters/falkordb.py's runtime probe
HOST="localhost"

say()  { printf '  %s\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m⚠\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; }

# ---- port helpers ---------------------------------------------
port_free() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ! ss -tlnH "sport = :$port" 2>/dev/null | grep -q ":$port"
  elif command -v lsof >/dev/null 2>&1; then
    ! lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
  else
    ! (echo > "/dev/tcp/127.0.0.1/$port") 2>/dev/null
  fi
}

who_uses() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null \
      | awk 'NR>1 {print $1 "/" $2}' | sort -u | tr '\n' ' '
  fi
}

pick_port() {
  # $1 = label (resp/browser), $2 = default start, $3 = explicit
  # override (empty = auto-pick), $4 = how many to try
  local label="$1" start="$2" explicit="$3" tries="${4:-10}"
  if [[ -n "$explicit" ]]; then
    if [[ "$explicit" == "0" ]]; then
      echo "0"; return 0
    fi
    port_free "$explicit" || {
      fail "requested $label port $explicit in use ($(who_uses $explicit))"
      return 1
    }
    echo "$explicit"; return 0
  fi
  local i
  for (( i = 0; i < tries; i++ )); do
    local try=$((start + i))
    if port_free "$try"; then
      [[ "$try" != "$start" ]] && warn "$label port $start in use ($(who_uses $start)) — using $try"
      echo "$try"; return 0
    fi
  done
  fail "no free $label port in $start..$((start + tries - 1)) — pin via env"
  return 1
}

# ---- recover existing container -------------------------------
# Container exists + running + GRAPH.QUERY working → no-op.
graph_query_works_at() {
  local port="$1"
  command -v docker >/dev/null 2>&1 || return 1
  docker run --rm --network host redis:7-alpine \
    redis-cli -h "$HOST" -p "$port" GRAPH.QUERY "$GRAPH" "RETURN 1" 2>&1 \
    | grep -q "1"
}

if command -v docker >/dev/null 2>&1; then
  if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    EXISTING_PORT=$(docker port "$CONTAINER_NAME" 6379/tcp 2>/dev/null | head -1 | sed 's/.*://')
    if [[ -n "$EXISTING_PORT" ]] && graph_query_works_at "$EXISTING_PORT"; then
      echo "$EXISTING_PORT" > "$PORT_FILE"
      ok "FalkorDB already running + healthy at $HOST:$EXISTING_PORT"
      exit 0
    fi
    warn "container '$CONTAINER_NAME' present but not responding — restarting"
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  elif docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    # Stopped or failed-to-start (e.g. previous port conflict). Force-remove
    # so we can re-create cleanly.
    say "removing stopped/failed container '$CONTAINER_NAME'..."
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  fi
fi

# ---- pick host ports ------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  fail "docker not found and FalkorDB isn't running."
  exit 1
fi

DEFAULT_PORT=6380
DEFAULT_BROWSER=3000
EXPLICIT_PORT="${FALKORDB_PORT:-}"
EXPLICIT_BROWSER="${FALKORDB_BROWSER_PORT:-}"

PORT="$(pick_port resp "$DEFAULT_PORT" "$EXPLICIT_PORT" 10)" || exit 1
BROWSER_PORT="$(pick_port browser "$DEFAULT_BROWSER" "$EXPLICIT_BROWSER" 10)" || exit 1

echo "$PORT" > "$PORT_FILE"
echo "$BROWSER_PORT" > "$BROWSER_FILE"

# ---- launch ---------------------------------------------------
browser_arg=()
if [ "$BROWSER_PORT" != "0" ]; then
  browser_arg=(-p "${BROWSER_PORT}:3000")
  say "starting $IMAGE — RESP on :$PORT, browser UI on :$BROWSER_PORT"
else
  say "starting $IMAGE — RESP on :$PORT (browser UI disabled)"
fi

docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  -p "${PORT}:6379" \
  "${browser_arg[@]}" \
  -v "memory-service-falkor-data:/var/lib/falkordb/data" \
  "$IMAGE" >/dev/null
ok "container started"
if [ "$BROWSER_PORT" != "0" ]; then
  ok "FalkorDB Browser: http://localhost:${BROWSER_PORT}/"
fi

# ---- healthcheck poll -----------------------------------------
for i in 1 2 3 4 5 6 7 8 9 10; do
  if graph_query_works_at "$PORT"; then
    ok "GRAPH.QUERY working at $HOST:$PORT (after ${i}s)"
    echo
    ok "FalkorDB ready"
    exit 0
  fi
  sleep 1
done

fail "FalkorDB started but GRAPH.QUERY isn't responding. Check container logs:"
fail "  docker logs $CONTAINER_NAME"
exit 1
