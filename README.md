# PR Sentinel

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

## Documentation

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — setup, style, testing, commits, PRs, branch protection
- [`COMMIT_CONVENTIONS.md`](COMMIT_CONVENTIONS.md) — Conventional Commits reference
- [`table.md`](table.md) — project charter (Russian)
