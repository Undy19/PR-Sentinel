# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]


### Added

- Docker deployment: `Dockerfile` (python:3.11-slim with git, non-root user,
  `HEALTHCHECK` against `GET /health`), `docker-compose.yaml` with a SQLite
  data volume, a `docker` build job in CI, and `docs/deployment.md` (TLS
  reverse proxy, GitHub webhook setup, secrets, backups, graph refresh,
  upgrade/rollback, capacity notes).
- Integration test `tests/test_expertise_integration.py`: builds the
  expertise graph from the tracked `test-repo` fixture (57 commits, 3
  authors) — the charter acceptance criterion for ≥50 commits / ≥3 authors.
- Latency observability: every processed PR logs per-stage timings
  (`fetch` / `analyze` / `recommend` / `send`) and the webhook-receipt-to-send
  total; exceeding `LATENCY_BUDGET_SECONDS` (default 10 s, the charter's
  notification target) logs a warning.
- Periodic expertise graph refresh: the graph is rebuilt from `REPO_PATH`
  every `GRAPH_REFRESH_INTERVAL_SECONDS` (default 3600 s, `0` disables), so
  commits merged after startup become visible without a restart.
- Reviewer login resolution: email local-parts are resolved to real GitHub
  logins via `GET /search/users?q="<email>" in:email` and cached in a new
  `login_map` table; failed lookups fall back to the local part. The
  `commits` table gains an `author_email` column (auto-migrated in place).

### Fixed

- Webhook standalone mode (`uvicorn pr_sentinel.webhook.server:app`) now builds
  the expertise graph from `REPO_PATH`; previously it started with an empty
  graph, so every notification silently recommended no reviewers. `REPO_PATH`
  is now a `Settings` field (`repo_path`, default `.`) shared by both entry
  points, and a failing graph build is logged without stopping the server.

## [0.1.0] - 2026-10-05

### Added

- PR Sentinel core: Telegram bot (aiogram 3.x) + FastAPI webhook for automated
  PR review filtering of a GitHub repository.
- LLM-based risk analyzer (OpenAI API): 3-line assessment with level
  `LOW` / `MED` / `HIGH` / `CRITICAL`, 1–3 specific reasons, and confidence;
  HTTP 429 rate limits retried with exponential backoff (2 s base, 30 s cap,
  max 3 retries); malformed model output degrades to a `HIGH` assessment
  instead of stalling the pipeline.
- Expertise graph: git history parsed into SQLite (`commits`,
  `file_expertise` tables); reviewer recommendations scored by touch
  frequency (40%) and recency with a 90-day half-life (60%).
- GitHub webhook endpoint `POST /webhook/github`: `X-Hub-Signature-256`
  verification, replay protection via `X-GitHub-Delivery` deduplication,
  5 MB body limit, asynchronous background processing (queue of 100).
- Notification composer: MarkdownV2 Telegram messages with ru/en
  localization (`NOTIFICATION_LANGUAGE`).
- SQLite persistence: PR history (`pr_history`) and seen webhook deliveries
  (`seen_deliveries`, 24 h retention).
- CI pipeline (`.github/workflows/ci.yaml`): ruff (lint + format), mypy (strict),
  pytest with a 60% coverage gate.
- commitlint enforcement of Conventional Commits on PRs and pushes to
  `main` (`.github/workflows/commitlint.yaml`).
- Project documentation: `README.md`, `CONTRIBUTING.md`,
  `COMMIT_CONVENTIONS.md`, `docs/architecture.md`.
- Local E2E and Telegram send test scripts (`scripts/`).
- `SECURITY.md` with the security policy and vulnerability reporting process.

### Changed

- `GITHUB_WEBHOOK_SECRET` is now required and no longer falls back to
  `GITHUB_TOKEN`.
- `TELEGRAM_BOT_TOKEN`, `GITHUB_TOKEN`, and `OPENAI_API_KEY` must be
  non-empty; `GITHUB_REPO` is validated as `owner/repo` with whitespace
  normalization.
- Configuration is loaded and validated centrally via `load_settings()` /
  `SettingsError`; both entry points (`python -m pr_sentinel.main`, `uvicorn`)
  print a human-readable list of problematic variables and exit cleanly.
