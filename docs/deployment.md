# Deployment Guide

Production deployment of PR Sentinel: one container running the Telegram bot
and the FastAPI webhook server (`python -m pr_sentinel.main`), behind a
reverse proxy with TLS.

## Prerequisites

- A Linux host (or Docker Desktop) with Docker + Docker Compose.
- A public domain name pointing at the host (e.g. `sentinel.example.com`).
- Ports 80/443 open; the app itself listens on 8000 and must not be exposed
  publicly without the proxy.
- A GitHub account token, Telegram bot token (from @BotFather), OpenAI API key.

## 1. Build the image

```bash
git clone https://github.com/Undy19/pr-sentinel && cd pr-sentinel
docker build -t pr-sentinel:0.1.0 .
```

The image contains git (required by the expertise-graph parser) and runs as a
non-root user. No secrets are baked in — all configuration comes from
environment variables at runtime.

## 2. Configure secrets

```bash
cp .env.example .env
$EDITOR .env
chmod 600 .env
```

Never commit `.env` and never pass secrets via `docker run -e` on shared
machines (they leak via `docker inspect`). Key variables: see the table in
[docs/architecture.md](architecture.md). `GITHUB_WEBHOOK_SECRET` must match
the Secret field of the GitHub webhook exactly.

## 3. Provide a git checkout for the expertise graph

The reviewer recommendations are built from `git log` of a local checkout of
`GITHUB_REPO`. Mount it read-only into the container and point `REPO_PATH`
at it (in `.env`: `REPO_PATH=/repo`):

```yaml
# docker-compose.yaml (excerpt)
volumes:
  - ./repo:/repo:ro
```

Keep the checkout updated on the host, e.g. a cron entry:

```cron
*/30 * * * * cd /srv/pr-sentinel/repo && git pull --ff-only
```

## 4. Run

```bash
docker compose up -d
docker compose logs -f sentinel
```

The SQLite database lives at `/data/pr_sentinel.db` inside the container,
backed by the `./data` volume — it survives container restarts and rebuilds.

## 5. Reverse proxy with TLS

Webhook delivery requires HTTPS with a valid certificate. Two supported
options:

### Caddy (automatic TLS)

```caddyfile
# Caddyfile
sentinel.example.com {
    reverse_proxy /webhook/github 127.0.0.1:8000
    reverse_proxy /health 127.0.0.1:8000
}
```

Caddy obtains and renews Let's Encrypt certificates automatically.

### nginx + certbot

```nginx
server {
    listen 443 ssl;
    server_name sentinel.example.com;
    ssl_certificate     /etc/letsencrypt/live/sentinel.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/sentinel.example.com/privkey.pem;

    location /webhook/github {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
    }
}
```

Bind the app to localhost only in this setup: `HOST=127.0.0.1` in `.env`
and drop the `ports:` mapping from compose (use `expose: ["8000"]` and proxy
through the compose network, or run nginx on the host with
`network_mode: "host"` for the app).

## 6. GitHub webhook setup

Repository → Settings → Webhooks → Add webhook:

- Payload URL: `https://sentinel.example.com/webhook/github`
- Content type: `application/json`
- Secret: the value of `GITHUB_WEBHOOK_SECRET`
- Events: let me choose → check **Pull requests** only

GitHub marks any `2xx` as delivered; the app answers `202 Accepted`
immediately and processes asynchronously.

## 7. Health check and monitoring

- `GET /health` → `{"status":"healthy"}` (no auth, leaks nothing).
- The Docker image declares a `HEALTHCHECK`; `docker ps` shows `healthy`.
- Logs: `docker compose logs sentinel` — every processed PR logs an accepted
  line and the notification pipeline; LLM retries/backoff are logged as
  warnings.

## 8. SQLite persistence, backup, restore

The database stores the expertise graph and PR history (replay protection).

Backup (safe online backup, no need to stop the container):

```bash
docker compose exec sentinel python -c \
  "import sqlite3; src=sqlite3.connect('/data/pr_sentinel.db'); \
   dst=sqlite3.connect('/data/backup.db'); src.backup(dst)"
chown <host-user> data/backup.db   # then move it off-host
```

Restore: stop the container, replace `data/pr_sentinel.db`, start again.
The expertise graph is fully derived from git history and can be rebuilt at
any time with `pr-sentinel index-repo` (see step 9); only `pr_history` and
the delivery-id log are unique data.

## 9. Refreshing the expertise graph

The graph is built at startup from `REPO_PATH` and then rebuilt automatically
every `GRAPH_REFRESH_INTERVAL_SECONDS` (default 3600; set `0` to disable), so
new merges become visible as long as the mounted checkout is updated (step 3).

To rebuild on demand instead of waiting for the interval:

```bash
docker compose exec sentinel pr-sentinel index-repo --repo-path /repo --db-path /data/pr_sentinel.db
```

## 10. Upgrade and rollback

- Upgrade: `docker build -t pr-sentinel:NEW .`, set `image: pr-sentinel:NEW`
  in compose, `docker compose up -d`.
- Rollback: point `image:` back at the previous tag and `docker compose up -d`.
  The SQLite schema is created with `CREATE TABLE IF NOT EXISTS`; data is
  preserved across versions.
- Pin tags in production (`pr-sentinel:0.1.0`), never deploy `latest`.

## 11. Capacity

The design targets 1 repository, ≤10 developers, ≤50 PRs/day. The webhook
worker processes PRs sequentially; the queue holds 100 items and answers
`503` when full (GitHub will redeliver). At the target load the queue stays
near-empty; if you see 503s, check OpenAI latency/retries in the logs first.
