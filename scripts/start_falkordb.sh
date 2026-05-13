#!/usr/bin/env bash
# scripts/start_falkordb.sh
#
# Idempotent FalkorDB bootstrap.
#
# Brings up a standalone `falkordb/falkordb` container on host port
# FALKORDB_PORT (from .env; default 6380 to avoid clashing with any
# plain-redis already running on 6379).
#
# Steps:
#   1. Read FALKORDB_HOST / FALKORDB_PORT from .env.
#   2. If something is already serving GRAPH.QUERY at that endpoint, do nothing.
#   3. Otherwise: docker run falkordb/falkordb in the background.
#   4. Probe GRAPH.QUERY to confirm the module is actually loaded.

set -euo pipefail
cd "$(dirname "$0")/.."

CONTAINER_NAME="${FALKORDB_CONTAINER:-memory-service-falkordb}"
IMAGE="${FALKORDB_IMAGE:-falkordb/falkordb:latest}"

# `|| true` on the end: a missing key makes grep exit 1, which combined
# with `set -euo pipefail` would kill the script on any optional .env key.
# Callers handle "no value" via the `:-default` fallback below.
get() { { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '"' | tr -d "'"; } || true; }
HOST="$(get FALKORDB_HOST)";       HOST="${HOST:-localhost}"
PORT="$(get FALKORDB_PORT)";       PORT="${PORT:-6380}"
# FalkorDB image bundles a Browser UI on container port 3000. Publish it
# on the host (FALKORDB_BROWSER_PORT, default 3000) so you can click
# through the graph at http://localhost:3000/. Set to 0 to disable.
BROWSER_PORT="$(get FALKORDB_BROWSER_PORT)"; BROWSER_PORT="${BROWSER_PORT:-3000}"
# Probe graph is hardcoded — the runtime healthcheck uses the same name
# (`adapters/falkordb.py`). We never write data here.
GRAPH="_healthcheck"

say()  { printf '  %s\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; }

# Probe GRAPH.QUERY (the actual capability we need, not just PING).
graph_query_works() {
  if ! command -v docker >/dev/null 2>&1; then return 1; fi
  # Use a transient redis-cli inside an alpine image so the host doesn't
  # need redis-cli installed.
  docker run --rm --network host redis:7-alpine \
    redis-cli -h "$HOST" -p "$PORT" GRAPH.QUERY "$GRAPH" "RETURN 1" 2>&1 \
    | grep -q "1" && return 0
  return 1
}

echo "FalkorDB target: $HOST:$PORT (graph=$GRAPH)"

if graph_query_works; then
  ok "FalkorDB already serving GRAPH.QUERY at $HOST:$PORT"
  exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
  fail "docker not found and FalkorDB not reachable at $HOST:$PORT"
  fail "Install Docker Desktop, or start FalkorDB manually:"
  fail "  docker run -d --name $CONTAINER_NAME -p $PORT:6379 $IMAGE"
  exit 1
fi

# If a stopped/abandoned container with our name exists, remove it.
if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
  if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    say "removing stopped container '$CONTAINER_NAME'..."
    docker rm "$CONTAINER_NAME" >/dev/null
  fi
fi

if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
  ok "container '$CONTAINER_NAME' running"
else
  browser_arg=()
  if [ "$BROWSER_PORT" != "0" ]; then
    browser_arg=(-p "${BROWSER_PORT}:3000")
    say "starting $IMAGE on host port $PORT (RESP) + $BROWSER_PORT (browser UI)..."
  else
    say "starting $IMAGE on host port $PORT (browser UI disabled)..."
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
fi

# Wait up to 10s for GRAPH.QUERY to come up.
for i in 1 2 3 4 5 6 7 8 9 10; do
  if graph_query_works; then
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
