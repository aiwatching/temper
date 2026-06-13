# syntax=docker/dockerfile:1.7
#
# Memory Service production image — multi-stage build.
#
# Stage 1 (builder): full build deps, install everything into a fresh
# venv so the final stage doesn't need pip, gcc, etc.
#
# Stage 2 (runtime): python:3.13-slim with just the venv + project
# code + entrypoint. Non-root user. HEALTHCHECK hits /v1/health.
#
# Build:  docker build -t memory-service:0.1.0 .
# Run:    see docker-compose.yml — needs DATABASE_URL, FALKORDB_*, LLM_*.

# -------- builder ----------
FROM python:3.13-slim AS builder

# Build deps for asyncpg + bcrypt + cryptography (manylinux wheels
# usually cover us, but pinning libpq + gcc handles edge cases).
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
 && rm -rf /var/lib/apt/lists/*

# Use a venv so we can copy /opt/venv into the slim runtime stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src/ src/

# Build arg for corporate networks with TLS-inspecting proxies that
# break pypi cert verification (self-signed CA in the chain). On a
# normal internet connection, leave this empty — keeps build-time
# TLS verification on.
#   docker build --build-arg PIP_TRUSTED_HOSTS=1 -t memory-service .
ARG PIP_TRUSTED_HOSTS=""
ENV PIP_TRUSTED_HOSTS=${PIP_TRUSTED_HOSTS}

# Anthropic extra is required for the LLM_PROVIDER=anthropic path.
RUN if [ -n "$PIP_TRUSTED_HOSTS" ]; then \
      TRUSTED="--trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host pypi.python.org"; \
    fi; \
    pip install --upgrade $TRUSTED pip \
 && pip install --no-cache-dir $TRUSTED . "graphiti-core[anthropic]"


# -------- runtime ----------
FROM python:3.13-slim AS runtime

# libpq5: asyncpg runtime. curl: HEALTHCHECK.
# postgresql-client: `pg_dump` / `pg_restore` for the in-app backup
# feature (POST /v1/admin/backups dumps Postgres over the network).
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
    libpq5 \
    postgresql-client \
    curl \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system app && useradd --system --gid app --home /app app

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
# Migrations + project source — needed by alembic + the import path.
COPY src/ /app/src/
COPY alembic.ini /app/alembic.ini

# Entrypoint runs migrations before booting uvicorn so the schema is
# always at head on container start. `exec` so signals (SIGTERM) reach
# the python process directly.
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER app

EXPOSE 18088

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:18088/v1/health | grep -q '"status":"ok"' || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "memory_service.main:app", "--host", "0.0.0.0", "--port", "18088"]
