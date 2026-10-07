# Architecture

PR Sentinel is a single-repository tool (≤10 developers, ≤50 PRs/day) that
turns every GitHub `pull_request` event into a short risk assessment and
reviewer recommendation posted to a Telegram chat.

## Data flow

```
GitHub pull_request webhook (opened / synchronize)
        ↓
FastAPI webhook server  (POST /webhook/github)
  • verify X-Hub-Signature-256 (HMAC)
  • reject >5 MB bodies (413)
  • dedupe by X-GitHub-Delivery (seen_deliveries, 24 h)
  • queue WorkItem (asyncio queue, max 100 → 503 when full)
        ↓
Background worker (sequential, one PR at a time)
        ↓
  fetch PR diff + changed files via GitHub REST API
        ↓
  ┌─────────────────────────┴────────────────────────┐
  ↓                                                  ↓
Risk analyzer                              Expertise graph
OpenAI chat model:                         SQLite lookup of file_expertise
level + 1–3 reasons + confidence           for the changed files:
429 → exponential backoff (2 s base,      score = 40% touch frequency
30 s cap, 3 retries); 45 s timeout;       + 60% recency (90-day half-life)
parse failure → HIGH fallback             top 1–2 reviewers; logins
                                          via GitHub /search/users,
                                          cached in login_map
  ↓                                                  ↓
  └─────────────────────────┬────────────────────────┘
                            ↓
Notification composer (MarkdownV2, ru/en labels)
                            ↓
Telegram bot (aiogram) → chat
                            ↓
SQLite: pr_history row
```

## Modules

| Module | File | Responsibility |
|---|---|---|
| Webhook server | `src/pr_sentinel/webhook/server.py` | FastAPI app, signature verification, replay protection, work queue, background worker, GitHub REST client, latency metrics, periodic graph refresh, reviewer login resolution |
| Risk analyzer | `src/pr_sentinel/analyzer/risk.py` | OpenAI call, JSON parsing, retry/backoff, `RiskAssessment` dataclass |
| Expertise graph | `src/pr_sentinel/graph/expertise.py` | `git log --name-only` parsing, `commits` / `file_expertise` / `login_map` tables, reviewer scoring |
| Notification composer | `src/pr_sentinel/notifications/composer.py` | MarkdownV2 message formatting with ru/en localization and escaping |
| Bot | `src/pr_sentinel/bot/bot.py` | aiogram 3.x session lifecycle, message sending with retries |
| Database | `src/pr_sentinel/db/database.py` | aiosqlite wrapper: `pr_history`, `seen_deliveries` |
| Config | `src/pr_sentinel/config.py` | pydantic-settings, all values from environment variables |
| Entry point | `src/pr_sentinel/main.py` | bot + webhook lifecycle wiring |
| CLI | `src/pr_sentinel/cli.py` | `index-repo` subcommand: rebuilds `commits`/`file_expertise` from git history |

## HTTP API

The web server exposes exactly two endpoints (port from the process; run with
`uvicorn pr_sentinel.webhook.server:app` or via `python -m pr_sentinel.main`):

### `GET /health`

Liveness probe. Returns `200` with `{"status": "healthy"}`.

### `POST /webhook/github`

Accepts GitHub `pull_request` webhook deliveries.

Required headers:

| Header | Value |
|---|---|
| `X-GitHub-Event` | `pull_request` (anything else → `400`) |
| `X-Hub-Signature-256` | `sha256=<hex>` — HMAC-SHA256 of the body with the required `GITHUB_WEBHOOK_SECRET` |
| `X-GitHub-Delivery` | delivery id, used for replay protection when `REPLAY_PROTECTION_ENABLED=true` |

Body: the standard GitHub `pull_request` event JSON with `action` in
`{opened, synchronize}` and a `pull_request` object containing
`number`, `title`, `html_url`.

Responses:

| Status | Meaning |
|---|---|
| `202` `{"status": "accepted"}` | queued for background analysis |
| `200` `{"status": "duplicate"}` | delivery id already seen within 24 h |
| `400` | wrong event type, bad action, invalid JSON, missing PR fields |
| `401` | signature mismatch |
| `413` | body larger than 5 MB |
| `503` | work queue full (100) — retry later |

## Data model (SQLite)

One database file (`DATABASE_PATH`, default `pr_sentinel.db`), five tables:

```sql
-- reviewed PRs (src/pr_sentinel/db/database.py)
CREATE TABLE pr_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_number INTEGER,
    title TEXT,
    url TEXT,
    risk_level TEXT,          -- LOW | MED | HIGH | CRITICAL
    timestamp TEXT            -- UTC ISO-8601
);

-- webhook replay protection, 24 h retention (src/pr_sentinel/db/database.py)
CREATE TABLE seen_deliveries (
    delivery_id TEXT PRIMARY KEY,
    seen_at TEXT              -- UTC ISO-8601
);

-- raw git history (src/pr_sentinel/graph/expertise.py)
CREATE TABLE commits (
    sha TEXT PRIMARY KEY,
    author TEXT NOT NULL,
    author_login TEXT NOT NULL,   -- local part of author email, else name
    author_email TEXT,            -- raw author email (added in place by ALTER on older DBs)
    files TEXT NOT NULL,
    timestamp TEXT NOT NULL       -- UTC ISO-8601
);

-- aggregated expertise (src/pr_sentinel/graph/expertise.py)
CREATE TABLE file_expertise (
    author_login TEXT NOT NULL,
    file_path TEXT NOT NULL,
    commit_count INTEGER NOT NULL DEFAULT 0,
    last_commit_ts TEXT NOT NULL,
    PRIMARY KEY (author_login, file_path)
);
CREATE INDEX idx_file_expertise_path ON file_expertise (file_path);

-- cached email→GitHub login resolutions (src/pr_sentinel/graph/expertise.py)
CREATE TABLE login_map (
    author_login TEXT PRIMARY KEY,
    github_login TEXT NOT NULL,
    resolved_at TEXT              -- UTC ISO-8601
);
```

All timestamps are normalized to UTC ISO-8601 so lexicographic order equals
chronological order.

`commits` and `file_expertise` are fully derived from git history and are
rebuilt (`DELETE` + full `INSERT`) at startup, on `index-repo`, and every
`GRAPH_REFRESH_INTERVAL_SECONDS`; `login_map` is preserved across rebuilds.

## Configuration

All configuration comes from environment variables (see `.env.example`):

| Variable | Required | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | Telegram bot API token |
| `GITHUB_TOKEN` | yes | GitHub REST API |
| `GITHUB_WEBHOOK_SECRET` | yes | dedicated HMAC key for `X-Hub-Signature-256` |
| `OPENAI_API_KEY` | yes | LLM risk analysis |
| `OPENAI_BASE_URL` | no | default `https://api.openai.com/v1` |
| `OPENAI_MODEL` | no | default `gpt-4o` |
| `DATABASE_PATH` | no | default `pr_sentinel.db` |
| `GITHUB_REPO` | yes | `owner/repo` of the analyzed repository |
| `TELEGRAM_CHAT_ID` | yes | destination chat for notifications |
| `NOTIFICATION_LANGUAGE` | no | `ru` (default) or `en` |
| `REPLAY_PROTECTION_ENABLED` | no | default `true` |
| `REPO_PATH` | no | default `.` — git repo path for the expertise graph |
| `LATENCY_BUDGET_SECONDS` | no | default `10` — notification latency budget; per-stage latency is logged per PR, exceeding the budget logs a warning |
| `GRAPH_REFRESH_INTERVAL_SECONDS` | no | default `3600` — periodic expertise graph rebuild; `0` disables |
| `HOST` | no | default `0.0.0.0` — webhook server bind host |
| `PORT` | no | default `8000` — webhook server port |

## Design constraints

- Single repository, single chat; no multi-tenancy.
- Async-first: aiogram 3.x, FastAPI, aiosqlite, httpx; one shared service
  bundle (`WebhookDeps`) injected via FastAPI dependencies.
- The LLM is the only non-deterministic stage; it is isolated in
  `src/pr_sentinel/analyzer/risk.py` with bounded retries, timeout, and a fallback
  result so the notification pipeline never blocks on it.
