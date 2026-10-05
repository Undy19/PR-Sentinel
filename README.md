# PR Sentinel
**English** | [Русский](README.ru.md)

[![CI](https://github.com/Undy19/pr-sentinel/actions/workflows/ci.yaml/badge.svg)](https://github.com/Undy19/pr-sentinel/actions/workflows/ci.yaml)
[![Version](https://img.shields.io/badge/version-0.1.0)](CHANGELOG.md)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A Telegram bot (aiogram 3.x) plus a FastAPI webhook that automates initial PR review
filtering for a GitHub repository. On every `pull_request` event it generates a concise
3-line risk assessment (LOW / MED / HIGH / CRITICAL) via the OpenAI API within ~60
seconds, then recommends 1–2 relevant reviewers from a git-history expertise graph
stored in SQLite.

> **🤖 AI-assisted development (vibe-coded).** The project concept was created by the
> project lead; the implementation was done with the help of AI (LLM-based coding
> agents). Humans reviewed and approved the final state.

## Tech stack

Python 3.11+, aiogram 3.x, FastAPI, httpx, OpenAI API, SQLite.

## Architecture

How the pipeline flows from a GitHub event to the Telegram chat:

```mermaid
flowchart TD
    A["GitHub pull_request webhook"] --> B["FastAPI webhook server<br/>(HMAC verify, replay dedupe, queue max 100)"]
    B --> C["Background worker"]
    C --> D["Risk analyzer<br/>(OpenAI, 429 retry)"]
    C --> E["Expertise graph<br/>(SQLite, git history)"]
    D --> F["Notification composer<br/>(ru/en)"]
    E --> F
    F --> G["Telegram bot"]
    G --> H["Chat"]
    C --> I["pr_history (SQLite)"]
```

## Features

- **Risk assessment** — LLM-based 3-line PR risk score (LOW / MED / HIGH / CRITICAL) with specific reasons and confidence, generated in ~60 s
- **Rate-limit handling** — OpenAI 429 retries with exponential backoff (2 s base, 30 s cap, 3 attempts)
- **Reviewer recommendation** — git-history expertise graph (SQLite) scores committers by touch frequency + recency; recommends 1–2 reviewers per PR
- **Webhook security** — `X-Hub-Signature-256` HMAC verification, `X-GitHub-Delivery` replay protection, 5 MB body limit
- **Async pipeline** — background processing queue (100 items); webhook acknowledges immediately, analysis runs async
- **Localization** — Telegram notifications in Russian (default) or English (`NOTIFICATION_LANGUAGE=en`)
- **CI/CD** — ruff (lint + format), mypy (strict), pytest (60 % coverage gate), commitlint (Conventional Commits)

## Quick start

```
pip install -e ".[dev]"
cp .env.example .env
```

```bash
python -m pr_sentinel.cli index-repo
```

Index the git history into the SQLite expertise graph used for reviewer recommendations. The graph is fully rebuilt from git history on every run — re-run after significant history changes. `--repo-path` and `--db-path` override `REPO_PATH` / `DATABASE_PATH`.

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | Telegram Bot API token |
| `GITHUB_TOKEN` | yes | GitHub PAT or fine-grained token. Read-only usage: the bot fetches PR diffs and file lists, it never writes to GitHub. Classic PAT: `repo` scope (required for private repos). Fine-grained: `Contents: Read` + `Pull requests: Read` on the target repository. |
| `GITHUB_WEBHOOK_SECRET` | yes | HMAC secret for `X-Hub-Signature-256` (set in GitHub webhook config) |
| `OPENAI_API_KEY` | yes | OpenAI API key (or `none` for local vLLM) |
| `OPENAI_BASE_URL` | no | Default `https://api.openai.com/v1` |
| `OPENAI_MODEL` | no | Default `gpt-4o` |
| `GITHUB_REPO` | yes | `owner/repo` to monitor |
| `TELEGRAM_CHAT_ID` | yes | Target chat/channel ID |
| `DATABASE_PATH` | no | Default `pr_sentinel.db` |
| `NOTIFICATION_LANGUAGE` | no | `ru` (default) or `en` |

Full reference: [`docs/architecture.md`](docs/architecture.md) § Configuration.

## Running the app

With the environment configured (see the table above), start the services:

**Bot + webhook in one process:**

```bash
python -m pr_sentinel.main
```

**Webhook server only:**

```bash
uvicorn pr_sentinel.webhook.server:app
```

**Run tests:**

```bash
pytest
```

**Run tests with the coverage gate (60% minimum):**

```bash
pytest --cov=pr_sentinel --cov-fail-under=60
```

For the full development workflow (lint, format, type-check, commit and PR process), see [`CONTRIBUTING.md`](CONTRIBUTING.md).

### Testing the webhook locally

GitHub delivers webhooks only to a public HTTPS URL. For local development, tunnel the server:

```bash
ngrok http 8000
```

(or localtunnel), then set `<tunnel-url>/webhook/github` as the webhook URL in GitHub repo settings (Settings → Webhooks → Add webhook; content type `application/json`; events: `pull_request`; secret = your `GITHUB_WEBHOOK_SECRET`). For production, deploy the server on a host with a public domain + SSL and point the webhook URL there, keeping the secret in sync.

## Example notification

What the bot posts to the chat for each new PR (template labels are in
Russian by default; `NOTIFICATION_LANGUAGE=en` switches them to English):

```
🔀 PR: Add retry on 429 rate-limit
📊 Риск: 🟡 Средний
💡 Изменения затрагивают аутентификацию • Добавлен код без тестов
👥 Ревьюеры: @alice, @bob
🔗 https://github.com/owner/repo/pull/42
```

## Documentation

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — setup, style, testing, commits, PRs, branch protection
- [`COMMIT_CONVENTIONS.md`](COMMIT_CONVENTIONS.md) — Conventional Commits reference
- [`SECURITY.md`](SECURITY.md) — security policy, vulnerability reporting, supported versions
- [`docs/architecture.md`](docs/architecture.md) — architecture, HTTP API, data model, configuration

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
