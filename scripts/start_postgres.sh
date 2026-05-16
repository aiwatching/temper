#!/usr/bin/env bash
# Bring up the dev Postgres (with the host-port exposure that the
# host-side uvicorn needs). Idempotent — safe to re-run.
#
# scripts/dev.sh expects this to have been run (or for you to
# already have a Postgres reachable at localhost:5432 with creds
# memory/memory). The Postgres in docker-compose.yml without this
# override stays internal-only for prod safety.

set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found in PATH — install Docker Desktop or equivalent first." >&2
  exit 1
fi

# Already running and healthy?
if docker ps --format '{{.Names}}' | grep -q '^temper-postgres-1$'; then
  if docker exec temper-postgres-1 pg_isready -U memory -d memory_service >/dev/null 2>&1; then
    echo "[postgres] already running + healthy at localhost:5432"
    exit 0
  fi
fi

echo "[postgres] starting docker compose db..."
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres
echo "[postgres] waiting for healthcheck..."
for _ in {1..30}; do
  if docker exec temper-postgres-1 pg_isready -U memory -d memory_service >/dev/null 2>&1; then
    echo "[postgres] ready at localhost:5432 (user=memory password=memory db=memory_service)"
    exit 0
  fi
  sleep 1
done
echo "[postgres] healthcheck timed out — inspect with: docker compose logs postgres" >&2
exit 1
