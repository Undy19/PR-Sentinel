# PR Sentinel

[![CI](https://github.com/Undy19/test/actions/workflows/ci.yaml/badge.svg)](https://github.com/Undy19/test/actions/workflows/ci.yaml)
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

Python 3.11+, aiogram 3.x, FastAPI, PyGithub, OpenAI API, SQLite.

## Quick start

```
pip install -e ".[dev]"
cp .env.example .env
```

Fill in the tokens in `.env`, then see `CONTRIBUTING.md` for running the bot, the
webhook server, and the test suite.

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
- [`docs/architecture.md`](docs/architecture.md) — architecture, HTTP API, data model, configuration

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
