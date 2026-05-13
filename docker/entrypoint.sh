#!/usr/bin/env bash
# Container entrypoint: run alembic migrations to head, then exec the CMD.
#
# Lives at /usr/local/bin/entrypoint.sh inside the image. The CMD
# (uvicorn ...) is appended via `exec "$@"` so signals (SIGTERM from
# `docker stop`) reach the python process directly, not via this shell.
#
# If migrations fail the container exits non-zero — fail-fast is the
# right posture; you don't want a service running against an old schema.
set -euo pipefail

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "entrypoint: DATABASE_URL is not set" >&2
  exit 64
fi

echo "entrypoint: applying alembic migrations…"
alembic upgrade head

# Pre-pull the embedding model if it's set and points at ollama —
# without it the first /v1/episodes call sees a cold cache. Best-effort:
# we don't fail the container if pull fails (e.g. ollama not reachable
# from inside the container, network blip).
if [[ "${EMBEDDING_PROVIDER:-}" == "ollama" && -n "${EMBEDDING_BASE_URL:-}" && -n "${EMBEDDING_MODEL:-}" ]]; then
  echo "entrypoint: warming embedding model ${EMBEDDING_MODEL}…"
  curl -fsS -X POST "${EMBEDDING_BASE_URL%/v1}/api/pull" \
    -d "{\"name\":\"${EMBEDDING_MODEL}\"}" >/dev/null 2>&1 || true
fi

echo "entrypoint: handing off to: $*"
exec "$@"
