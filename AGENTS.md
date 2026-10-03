# Repository Guidelines

## Project Overview

**PR Sentinel** — a Telegram bot that automates initial PR review filtering for GitHub repositories. It analyzes new pull requests within 60 seconds, generates a 3-line risk assessment (LOW/MED/HIGH/CRITICAL) using OpenAI's API, and recommends 1–2 relevant reviewers based on commit-history expertise.

**Purpose:** Solve the code-review bottleneck where PRs wait hours/days for human review.

**Scope:** Single repository, ≤10 developers, ≤50 PRs/day.

**Current State:** Specification only. No source code exists. The only file in the repository is `table.md` (project charter in Russian).

## Architecture & Data Flow

Planned architecture (not yet implemented):

```
GitHub pull_request event
        ↓
FastAPI webhook server (HTTP endpoint)
        ↓
   ┌─────────────────────────────────┐
   ↓                                 ↓
Risk Analyzer              Expertise Graph Builder
(OpenAI API → LLM)         (Git history parser → SQLite)
   ↓                                 ↓
   └────────────┬────────────────────┘
                ↓
        Notification Composer
                ↓
    Telegram Bot (aiogram 3.x)
                ↓
    Risk score + rationale + reviewer recommendations
```

### Key Modules (planned)
1. **Telegram Bot** — receives/sends messages, user interaction via aiogram 3.x
2. **Webhook Receiver** — FastAPI HTTP endpoint for GitHub `pull_request` events (opened/synchronize)
3. **Risk Analyzer** — LLM-based PR risk scoring using OpenAI API; requires rate-limit handling and retry logic
4. **Expertise Graph Builder/Recommender** — parses git history, builds expertise graph, recommends reviewers
5. **SQLite Persistence** — stores expertise graph and PR history

### Data Flow
1. GitHub fires `pull_request` webhook → FastAPI endpoint
2. System fetches PR details via PyGithub
3. Risk Analyzer sends PR diff to OpenAI for risk scoring
4. Expertise Graph queries SQLite for relevant committers
5. Notification Composer formats message with risk emoji, rationale, reviewers
6. aiogram bot sends formatted message to Telegram channel

## Key Directories

No source directories exist yet. Planned structure (inferred from spec):
- `src/` — main application code (bot, webhook server, analyzer, graph builder)
- `tests/` — unit and integration tests
- `scripts/` — development and deployment scripts
- `docs/` — documentation

## Development Commands

No build system exists yet. Expected commands (inferred from Python tech stack):

| Operation | Command (planned) |
|-----------|-------------------|
| Install dependencies | `pip install -r requirements.txt` or `pip install -e .` |
| Run bot (dev) | `python src/bot.py` |
| Run webhook server | `uvicorn src.webhook:app` |
| Run tests | `pytest` |
| Run tests with coverage | `pytest --cov=src --cov-fail-under=60` |
| Format code | `black src/ tests/` |
| Lint code | `ruff check src/ tests/` |
| Type check | `mypy src/` |
| Build Docker image | `docker build -t pr-sentinel .` |

## Code Conventions & Common Patterns

**Language:** Python 3.11+

**Error Handling:**
- OpenAI API calls MUST have rate-limit handling and retry logic (spec requirement)
- All external API calls (GitHub, OpenAI) MUST handle timeouts

**Async Patterns:**
- Async-first architecture (aiogram 3.x and FastAPI both use asyncio)
- Use `async def` for all bot handlers and webhook endpoints
- Use `aiohttp` or `httpx` for async HTTP calls

**State Management:**
- SQLite for persistent state (expertise graph, PR history)
- Use context managers or DI for database connections
- No in-memory global state

**Dependency Injection:**
- Pass dependencies explicitly (functions/constructors) rather than global singletons
- Configuration loaded from environment variables

**Naming:**
- Python standard conventions (PEP 8)
- `snake_case` for functions, variables, modules
- `CamelCase` for classes

**Configuration:**
- Use environment variables for secrets (TELEGRAM_BOT_TOKEN, GITHUB_TOKEN, OPENAI_API_KEY)
- Load config at startup, not inline

**Commits:**
- Follow Conventional Commits 1.0: `<type>(<scope>): <description>`
- Allowed types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`
- Enforced in CI via commitlint on PRs and pushes to `main`
- Full reference: `COMMIT_CONVENTIONS.md`
- Examples: `feat(analyzer): add retry on 429`, `fix(bot): close session on shutdown`

## Important Files

| File | Purpose |
|------|---------|
| `table.md` | Project charter/specification (Russian) — the source of truth for requirements, acceptance criteria, and scope |
| `COMMIT_CONVENTIONS.md` | Commit message reference (Conventional Commits 1.0) |
| `CONTRIBUTING.md` | Contributor guide (setup, style, testing, commits, PRs) |
| `.github/PULL_REQUEST_TEMPLATE.md` | PR template |
| `.github/workflows/commitlint.yaml` | Commit message CI check (commitlint) |

## Runtime/Tooling Preferences

- **Language:** Python 3.11+
- **Package Manager:** pip (with requirements.txt or pyproject.toml)
- **Runtime:** Standard CPython; no Bun/Node/Deno
- **Database:** SQLite (stdlib, no external server)
- **Deployment:** Docker container
- **CI/CD:** Configured — GitHub Actions workflow at `.github/workflows/ci.yaml` (ruff, black, mypy, pytest with 60% coverage gate)
- **External APIs:** GitHub API (via PyGithub), OpenAI API

## Testing & QA

**Requirements from spec:**
- ≥60% code coverage
- Unit tests + integration tests
- All critical paths must be covered

**Expected Test Setup:**
- Framework: `pytest` (Python standard)
- Coverage: `pytest-cov` with `--cov-fail-under=60`
- Async tests: `pytest-asyncio`
- Mocking: `unittest.mock` or `responses` for HTTP mocks (GitHub/OpenAI APIs)

**Key Test Scenarios (from acceptance criteria):**
1. Bot responds to `pull_request` events within ≤5 seconds (performance test)
2. Every notification contains: PR title, risk level emoji, 1–2 risk reasons, recommended reviewers (integration test)
3. Expertise graph builds correctly on repos with ≥50 commits and ≥3 authors (integration test with fixtures)
4. LLM analysis handles rate limits with retry logic (unit test with mocked API)
5. Pilot testing with ≥4 developers (manual acceptance)

## File Deletion Policy

- When deleting or removing any files/directories, ALWAYS move them to the **Windows Recycle Bin** instead of permanent deletion.
- Use Python's `send2trash` library: `pip install send2trash` → `send2trash.send2trash(path)`
- NEVER use `rm`, `del`, or `os.remove()` for project files.
- The user will clean the Recycle Bin manually.
