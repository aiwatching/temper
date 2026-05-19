#!/usr/bin/env bash
# Bring up the dev Postgres on a host port that's actually free.
#
# Strategy:
#   1. If our own container (temper-postgres-1) is already running +
#      healthy → no-op (read its host port from `docker ps`).
#   2. Probe 5432. If free → use it.
#   3. Else probe 5433, 5434, …, 5439. First free one wins.
#   4. Write the chosen port to `.data/postgres-port` so other
#      scripts (start.sh, dev.sh, install.sh) build the right
#      DATABASE_URL.
#   5. Boot docker compose with HOST_POSTGRES_PORT set to the
#      chosen value; docker-compose.dev.yml maps it to the
#      container's 5432.
#
# Idempotent — safe to re-run. Override the port range by setting
# POSTGRES_PORT to a specific value (skips probing).

set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p .data
PORT_FILE=".data/postgres-port"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found in PATH — install Docker Desktop or equivalent first." >&2
  exit 1
fi

# docker-compose.yml uses `${POSTGRES_PASSWORD:?...must be set...}` —
# host shell env, evaluated before override merge.
export POSTGRES_USER="${POSTGRES_USER:-memory}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-memory}"
export POSTGRES_DB="${POSTGRES_DB:-memory_service}"

# Returns 0 (success) if $1 is free, 1 if in use. Tries ss → lsof →
# pure-bash /dev/tcp fallback so we don't depend on a specific tool.
port_free() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ! ss -tlnH "sport = :$port" 2>/dev/null | grep -q ":$port"
  elif command -v lsof >/dev/null 2>&1; then
    ! lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
  else
    # bash builtin — tries to connect; if connect succeeds something
    # is listening. Slight timeout via subshell + bg.
    ! (echo > "/dev/tcp/127.0.0.1/$port") 2>/dev/null
  fi
}

# ---- 1. existing container? ------------------------------------
if docker ps --format '{{.Names}}' | grep -q '^temper-postgres-1$'; then
  if docker exec temper-postgres-1 pg_isready -U memory -d memory_service >/dev/null 2>&1; then
    # Recover host port from docker ps so DATABASE_URL stays right
    # across restarts.
    EXISTING_PORT=$(docker port temper-postgres-1 5432/tcp 2>/dev/null | head -1 | sed 's/.*://')
    if [[ -n "$EXISTING_PORT" ]]; then
      echo "$EXISTING_PORT" > "$PORT_FILE"
      echo "[postgres] already running + healthy at localhost:$EXISTING_PORT"
      exit 0
    fi
  fi
fi

# ---- 2/3. pick a free port -------------------------------------
if [[ -n "${POSTGRES_PORT:-}" ]]; then
  CHOSEN="$POSTGRES_PORT"
  port_free "$CHOSEN" || {
    echo "[postgres] requested port $CHOSEN is in use." >&2
    echo "          run \`lsof -i :$CHOSEN\` (or \`ss -tlnp\`) to see who." >&2
    exit 1
  }
else
  CHOSEN=""
  for try in 5432 5433 5434 5435 5436 5437 5438 5439; do
    if port_free "$try"; then
      CHOSEN="$try"
      break
    fi
  done
  if [[ -z "$CHOSEN" ]]; then
    echo "[postgres] no free port in 5432..5439 — set POSTGRES_PORT=NNNN explicitly." >&2
    exit 1
  fi
fi

if [[ "$CHOSEN" != "5432" ]]; then
  echo "[postgres] 5432 in use — using $CHOSEN instead"
  if command -v lsof >/dev/null 2>&1; then
    echo "          (who's on 5432: $(lsof -nP -iTCP:5432 -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print $1 "/" $2}' | sort -u | tr '\n' ' '))"
  fi
fi
echo "$CHOSEN" > "$PORT_FILE"

# ---- 4. boot docker compose with the chosen port --------------
export HOST_POSTGRES_PORT="$CHOSEN"
echo "[postgres] starting docker compose db on :$CHOSEN..."
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres

# ---- 5. healthcheck poll --------------------------------------
echo "[postgres] waiting for healthcheck..."
for _ in {1..30}; do
  if docker exec temper-postgres-1 pg_isready -U memory -d memory_service >/dev/null 2>&1; then
    echo "[postgres] ready at localhost:$CHOSEN (user=memory password=memory db=memory_service)"
    exit 0
  fi
  sleep 1
done
echo "[postgres] healthcheck timed out — inspect with: docker compose logs postgres" >&2
exit 1
