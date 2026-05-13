# Deploy

Production-shaped deploy of Memory Service, intended for a single host
running docker-compose behind nginx. Adapt to k8s by treating the
compose file as a service contract — the env vars and ports are the
same.

## Requirements

- Docker 24+ with compose v2
- Reachable LLM endpoint (`LLM_PROVIDER` + key)
- Reachable embedding endpoint (`EMBEDDING_PROVIDER` + key/url)
- Public DNS + TLS cert if you're putting nginx in front

## First boot

```bash
git clone <repo> memory-service && cd memory-service
cp .env.prod.example .env.prod
$EDITOR .env.prod   # fill in the REQUIRED section
docker compose --env-file .env.prod up -d --build
curl http://localhost:8000/v1/health
```

Health response must show every component `ok: true`:

```json
{"status":"ok","checks":{"postgres":{"ok":true,...},"falkordb":{...},"graphiti":{...}}}
```

If `graphiti.ok` is false, the LLM or embedding endpoint isn't
reachable from inside the container. `docker compose logs memory-service`
shows what it tried.

## Required env vars

All of these must be set in `.env.prod`. The container refuses to
boot otherwise.

| Var | What |
|---|---|
| `SECRET_KEY` | Random 32+ bytes. `openssl rand -base64 48` |
| `POSTGRES_PASSWORD` | Password the postgres container is created with — must match what the service uses (compose builds DATABASE_URL from this) |
| `LLM_PROVIDER` | One of openai \| deepseek \| anthropic \| ollama |
| `LLM_API_KEY` | Provider's key (or any non-empty string for ollama) |
| `EMBEDDING_PROVIDER` | One of openai \| ollama |
| `EMBEDDING_API_KEY` | If openai |

Optional but **strongly recommended**:

| Var | What |
|---|---|
| `LOG_FORMAT=json` | Ships single-line JSON to stderr, ready for log aggregators |
| `GRAPHITI_TELEMETRY_ENABLED=false` | Disables Graphiti's PostHog ping |
| `CORS_ALLOW_ORIGINS=https://...` | Comma-separated origin allowlist for any browser frontend |
| `BOOTSTRAP_SUPER_ADMIN_EMAIL` | First user with this email becomes super_admin on register |
| `MS_BIND=127.0.0.1` | (default) loopback-only; put nginx in front. `0.0.0.0` if you really want the port open |
| `SEARCH_RERANKER=rrf` | (default) free reranker. `cross_encoder` uses LLM per query — slower but better |

## What's exposed

| Port | Reachable from | What |
|---|---|---|
| 8000 (host) | depends on `MS_BIND` | Memory Service REST API |
| 3000 (host) | `127.0.0.1` only | FalkorDB Browser UI (graph viz) |
| postgres | internal compose net only | not exposed to host |
| falkordb RESP | internal compose net only | not exposed to host |

If your host has a firewall and you're not putting nginx in front,
you'll need to open whatever `MS_PORT` resolves to.

## TLS + reverse proxy

`docker/nginx-example.conf` is a working starting point. Key choices:

- TLS terminated at nginx, plaintext on loopback to Memory Service.
- `client_max_body_size 16M` to accommodate the bulk-write endpoint
  (200 episodes × up to 64KB each).
- `proxy_read_timeout 120s` so `?reranker=cross_encoder` queries
  (which can take 30+s) don't get killed.

```bash
sudo cp docker/nginx-example.conf /etc/nginx/sites-available/memory-service
sudo ln -s /etc/nginx/sites-available/memory-service /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

## Upgrading

```bash
git pull
docker compose --env-file .env.prod build memory-service
docker compose --env-file .env.prod up -d memory-service
```

The container entrypoint runs `alembic upgrade head` on every boot.
It's safe to roll back code, but **never roll back a migration on
production data** — Alembic's `downgrade` paths are scaffolded but
not all of them are tested.

## Backups

Two stateful volumes:

```bash
# Postgres dump
docker compose --env-file .env.prod exec postgres pg_dump -U memory memory_service \
  | gzip > /backups/ms-$(date +%F).sql.gz

# FalkorDB snapshot — BGSAVE writes to /var/lib/falkordb/data inside the
# container; the named volume `falkordb_data` is where it lands.
docker compose --env-file .env.prod exec falkordb redis-cli BGSAVE
# Then cp the volume contents off the host:
docker run --rm \
  -v temper_falkordb_data:/data -v /backups:/out \
  alpine tar czf /out/falkor-$(date +%F).tar.gz -C /data .
```

Restore is the reverse: drop the volume, untar, then `pg_restore` /
let FalkorDB pick up the dump at startup.

## Operating tips

- **First request after boot is slow** — Graphiti caches indexes lazily.
  Send a throwaway `/v1/health` to warm it.
- **`docker compose logs -f memory-service`** shows the request log
  and any extraction errors. With `LOG_FORMAT=json` pipe through `jq`.
- **Wipe everything** (DON'T do this on prod data):
  ```bash
  docker compose --env-file .env.prod down -v
  ```
- **Service stops responding** but health was green → check
  `docker stats` for memory. The Graphiti + FalkorDB combo can grow
  with graph size; allocate 2GB+ for the service container.

## What's NOT shipped here

These are deliberately not included; add when you need them:

- **Auto-renewing certs**: bring your own `certbot` or use Caddy
  instead of nginx if you want auto-TLS.
- **Metrics endpoint**: no `/metrics` for Prometheus yet. Easy to add
  with `prometheus-fastapi-instrumentator`.
- **K8s manifests**: the compose file is the source of truth; port to
  helm chart or kustomize as a separate step.
- **Multi-region / HA**: single Postgres + single FalkorDB. For HA
  you'd front Postgres with a replica setup and use FalkorDB Cloud
  (the open-source image is single-node).
