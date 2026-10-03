# Contributing to PR Sentinel

Thank you for your interest in contributing! This guide covers everything you need to get started:
setting up your environment, following our code style, running tests, writing commits, and
submitting pull requests.

## Getting Started

**Prerequisites:**

- Python 3.11+
- pip

**Setup:**

```bash
pip install -e ".[dev]"
```

This installs the project in editable mode along with all development dependencies (pytest,
ruff, black, mypy, etc.).

**Environment variables:**

Copy the example env file and fill in your local secrets:

```bash
cp .env.example .env
```

Required variables:

- `TELEGRAM_BOT_TOKEN` — Telegram bot API token
- `GITHUB_TOKEN` — GitHub personal access token
- `GITHUB_WEBHOOK_SECRET` — Secret for verifying GitHub webhook signatures
- `OPENAI_API_KEY` — OpenAI API key for risk analysis

## Code Style

We enforce consistent formatting and type safety on every commit:

| Tool  | Command                 | Purpose                          |
| ----- | ----------------------- | -------------------------------- |
| Black | `black src/ tests/`     | Auto-formatting (line-length 100) |
| Ruff  | `ruff check src/ tests/` | Linting                          |
| Mypy  | `mypy src/`             | Static type checking (strict)    |

Run all three before submitting a PR:

```bash
black src/ tests/
ruff check src/ tests/
mypy src/
```

## Testing

**Run all tests:**

```bash
pytest
```

**Run with the coverage gate (60% minimum):**

```bash
pytest --cov=src --cov-fail-under=60
```

- Async tests run in `pytest-asyncio` auto mode — no `@pytest.mark.asyncio` decorators needed.
- All external APIs (Telegram, GitHub, OpenAI) are **mocked** in tests. No network access is
  required.

## Commits

We follow **Conventional Commits 1.0**. Every commit message must use the
`<type>(<scope>): <description>` format.

Allowed types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`,
`chore`, `revert`.

Examples:

```
feat(analyzer): add retry on 429 rate-limit
fix(webhook): handle missing X-GitHub-Event header
test: add unit tests for expertise graph
feat(bot)!: change notification format
```

Breaking changes are indicated with `!` after the type/scope or a `BREAKING CHANGE:` footer.

See [`COMMIT_CONVENTIONS.md`](COMMIT_CONVENTIONS.md) for the full reference.

**CI enforcement:** commitlint validates commit messages on all PRs and on pushes to `main`.
Non-conforming commits will fail CI.

## Pull Requests

1. **Use the PR template** at [`.github/PULL_REQUEST_TEMPLATE.md`](.github/PULL_REQUEST_TEMPLATE.md)
   — fill in every section.
2. **Keep PRs small and focused** — one logical change per PR.
3. **CI must be green** before requesting review. The required checks are:
   - `ruff` — linting
   - `black` — formatting
   - `mypy` — type checking
   - `tests` — pytest with 60% coverage gate
   - `commitlint` — commit message format
4. **Branch protection** on `main` requires the `CI` and `commitlint` checks to pass. Do not
   merge with failing checks.

For larger features, open an issue first to discuss the approach before starting implementation.

## Branch Protection (Ruleset)

`main` is protected by a GitHub **branch ruleset** (not by any file in this repo). If you are
setting up a fresh fork, recreate it with these steps:

1. In GitHub's web UI, open the repository and go to **Settings → Rulesets → Create
   ruleset** → **Create new ruleset**.
2. Give it a name (e.g. `Protect main`). For **Source branch**, select *Main branch*;
   for **Rule type**, select **Branch**.
3. Under **Target**, choose *Specific branches* and enter `main`.
4. In the **Rules** (Evaluate) tab, enable exactly the following:
   - **Require a pull request before merging** — Required approvals: 1; turn on
     **Dismiss stale pull request approvals when new commits are pushed**; under
     **Allowed merge methods** enable **Squash** only (disable Merge and Rebase —
     squash keeps the history clean with Conventional Commits)
   - **Require branches to be up to date before merging**
   - **Require status checks to pass before merging** — check the boxes for the two CI
     checks named exactly `CI` and `Commitlint`
   - **Block force pushes** — prevents history rewriting on `main`
   - Optional (stricter review): **Require conversation resolution before merging**
     (inside the pull-request rule) and **Require linear history**
   - Leave everything else off: *Restrict creations/updates/deletions*, *Require
     deployments to succeed*, *Require signed commits*, *Require code scanning results*,
     *Require code quality results*, *Restrict code coverage* (coverage is already
     gated in CI via `pytest --cov=src --cov-fail-under=60`), and the Copilot options
     (preview features that consume quota).
5. Leave **Custom bypass** at its default: administrators.
6. In the **Enforcement** section, keep **This ruleset would be enforced** selected.
7. **Save** the ruleset. It is active immediately: direct pushes to `main` and merges with
   red status checks are blocked.

Notes:

- The required-check names must match the workflow job names **exactly**: `CI`
  (`.github/workflows/ci.yaml`) and `Commitlint` (`.github/workflows/commitlint.yaml`).
- If you rename a job in either workflow later, update the ruleset to match, or the
  required checks will fail.
- This is a one-time repository setting in GitHub's UI, not a file checked into the repo.
