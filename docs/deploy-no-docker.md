# Deploying TEMPER without Docker

The default `./install.sh` flow uses Docker for Postgres + FalkorDB. On
a production server where you'd rather run native services (apt
packages, managed RDS, etc.), use `--no-docker` mode and bring your
own databases. TEMPER itself runs as a regular Python process — same
in either deploy.

This guide assumes Ubuntu 22.04+. RHEL / Alpine should be similar but
package names differ.

---

## 1. Postgres 16

```bash
# Add the PGDG repo (Ubuntu's apt-postgresql often lags upstream).
sudo apt update && sudo apt install -y curl ca-certificates gnupg
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
  | sudo gpg --dearmor -o /usr/share/keyrings/postgresql.gpg
echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
  | sudo tee /etc/apt/sources.list.d/postgresql.list

sudo apt update && sudo apt install -y postgresql-16 postgresql-client-16

# Create the temper role + database.
sudo -u postgres psql <<SQL
CREATE USER memory WITH PASSWORD 'change-me-to-something-strong';
CREATE DATABASE memory_service OWNER memory;
GRANT ALL PRIVILEGES ON DATABASE memory_service TO memory;
SQL

# (Optional) bind to localhost only — typical for an app server that
# co-locates Postgres. Edit /etc/postgresql/16/main/postgresql.conf:
#   listen_addresses = 'localhost'

sudo systemctl enable --now postgresql
sudo systemctl status postgresql       # green = good
```

If Postgres lives on a separate host (RDS / managed instance / dedicated
DB box), skip this — just have the connection string ready.

---

## 2. FalkorDB

FalkorDB is a Redis module. Three deploy options, easiest first:

### Option A — Docker container even when TEMPER isn't dockerized

Cleanest for most teams. Run FalkorDB in docker on the same box,
TEMPER as a native process talking to it over loopback:

```bash
docker run -d \
  --name falkordb --restart unless-stopped \
  -p 127.0.0.1:6379:6379 \
  -v falkordb-data:/var/lib/falkordb/data \
  falkordb/falkordb:latest
```

### Option B — Native install via `redis-server` + module

```bash
sudo apt install -y redis-server build-essential cmake git

# Build the module.
git clone --recursive https://github.com/FalkorDB/FalkorDB.git
cd FalkorDB && make && sudo cp bin/src/falkordb.so /usr/lib/redis/modules/

# Tell redis to load it.
sudo tee -a /etc/redis/redis.conf <<EOF

loadmodule /usr/lib/redis/modules/falkordb.so
EOF

sudo systemctl restart redis-server
sudo systemctl enable redis-server

# Verify.
redis-cli MODULE LIST | grep -i graph    # should show the loaded module
```

### Option C — Hosted FalkorDB

[FalkorDB Cloud](https://www.falkordb.com/) provides managed instances.
TEMPER just needs the `FALKORDB_HOST` + `FALKORDB_PORT` + optional
`FALKORDB_PASSWORD`.

---

## 3. Configure `.env`

After Postgres + FalkorDB are reachable, point TEMPER at them:

```bash
cd ~/temper

# install.sh writes a sane .env on first run, but in BYO mode you
# need to verify these specifically:
$EDITOR .env

# Required edits:
#   DATABASE_URL=postgresql+asyncpg://memory:STRONG-PASSWORD@<host>:5432/memory_service
#   FALKORDB_HOST=<host>          # 127.0.0.1 if local
#   FALKORDB_PORT=6379            # native default (docker convention is 6380)
#   FALKORDB_PASSWORD=            # set if you AUTH'd redis
#   SECRET_KEY=<48-byte random>   # MUST be a real value, not the placeholder
#
# Recommended for prod:
#   APP_ENV=production
#   ALLOW_SELF_REGISTRATION=false
#   CREATE_DEFAULT_ADMIN=false    # use BOOTSTRAP_SUPER_ADMIN_EMAIL instead
#   LOG_FORMAT=json
#   CORS_ALLOW_ORIGINS=https://your-frontend.example.com
```

Generate a strong SECRET_KEY one-liner:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

---

## 4. Install + migrate

```bash
./install.sh --no-docker
```

What `--no-docker` does:

1. uv + Python interpreter (same as docker mode)
2. `uv sync` for deps
3. **No** docker compose calls
4. Verifies Postgres reachable via `pg_isready` (skip with warn if
   `pg_isready` missing — `apt install postgresql-client` to enable)
5. Verifies FalkorDB reachable via `redis-cli PING` (skip with warn
   if missing — `apt install redis-tools` to enable)
6. `alembic upgrade head` against your DATABASE_URL

If migrations fail, the most common cause is `DATABASE_URL`
misconfigured. Verify with:

```bash
psql "$DATABASE_URL" -c '\dt'      # should connect, may show no tables
```

---

## 5. Run as a service (systemd)

For a long-running prod deploy, manage TEMPER with systemd. Create
`/etc/systemd/system/temper.service`:

```ini
[Unit]
Description=TEMPER memory service
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User=temper
Group=temper
WorkingDirectory=/srv/temper
EnvironmentFile=/srv/temper/.env
ExecStart=/srv/temper/.venv/bin/uvicorn memory_service.main:app --host 127.0.0.1 --port 18088
Restart=on-failure
RestartSec=5

# Security hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/srv/temper/.data

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo useradd -r -d /srv/temper -s /bin/bash temper
sudo chown -R temper:temper /srv/temper

sudo systemctl daemon-reload
sudo systemctl enable --now temper
sudo systemctl status temper

# Logs
sudo journalctl -u temper -f
```

`./start.sh`'s background mode + log-rotation is fine for "let me run
it manually for a few weeks", but systemd is the right answer for any
deploy you want to survive reboots / OOM kills cleanly.

---

## 6. Put it behind nginx / TLS

TEMPER doesn't terminate TLS. Put nginx (or Caddy / Traefik) in front:

```nginx
# /etc/nginx/sites-available/temper
server {
    listen 443 ssl http2;
    server_name temper.example.com;

    ssl_certificate     /etc/letsencrypt/live/temper.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/temper.example.com/privkey.pem;

    # Larger uploads for /v1/documents/import + bulk_episodes.
    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:18088;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE streams (we don't have these yet but reserved for future
        # /chat / /turn_context push). Disable proxy buffering so
        # tokens flow immediately.
        proxy_buffering off;
        proxy_cache off;
    }
}
```

```bash
sudo certbot --nginx -d temper.example.com
```

---

## 7. Backups

`pg_dump` covers everything except FalkorDB:

```bash
# Daily Postgres dump rotation
0 3 * * * pg_dump --no-owner --no-privileges memory_service \
  | gzip > /var/backups/temper/memory_service-$(date +\%F).sql.gz \
  && find /var/backups/temper -name 'memory_service-*.sql.gz' -mtime +14 -delete

# FalkorDB — it's redis, use BGSAVE + ship the .rdb file
0 3 * * * redis-cli -h $FALKORDB_HOST -p $FALKORDB_PORT BGSAVE
0 4 * * * cp /var/lib/redis/dump.rdb /var/backups/temper/falkor-$(date +\%F).rdb
```

Restoring:

```bash
gunzip -c memory_service-2026-05-17.sql.gz | psql memory_service
sudo systemctl stop redis-server
sudo cp falkor-2026-05-17.rdb /var/lib/redis/dump.rdb
sudo chown redis:redis /var/lib/redis/dump.rdb
sudo systemctl start redis-server
```

---

## 8. Updates

```bash
cd /srv/temper
git pull
./update.sh --no-pull          # already pulled
# OR explicitly:
# uv sync && uv run alembic upgrade head
sudo systemctl restart temper
```

`update.sh` handles `--no-docker` correctly — it just doesn't try to
bounce docker containers.

---

## Local dev stays on Docker

`./install.sh` (no flags) keeps using Docker for the local laptop
workflow — `--no-docker` is purely additive. The same checkout can
serve both: dev on your laptop with docker, prod on the server
without.
