# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
- Security audit reports (source code, dependencies, consolidated).
- Local E2E and Telegram send test scripts (`scripts/`).
- `SECURITY.md` with the security policy and vulnerability reporting process.

### Changed

- `GITHUB_WEBHOOK_SECRET` is now required and no longer falls back to
  `GITHUB_TOKEN`. **Breaking change.**
- `TELEGRAM_BOT_TOKEN`, `GITHUB_TOKEN`, and `OPENAI_API_KEY` must be
  non-empty; `GITHUB_REPO` is validated as `owner/repo` with whitespace
  normalization. **Breaking change.**
- Configuration is loaded and validated centrally via `load_settings()` /
  `SettingsError`; both entry points (`python -m pr_sentinel.main`, `uvicorn`)
  print a human-readable list of problematic variables and exit cleanly.
