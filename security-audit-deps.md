# Security Audit — Tests, Scripts, Config, Dependencies

Project: **PR Sentinel** (Telegram bot + FastAPI GitHub webhook + OpenAI + SQLite)
Scope: `requirements.txt`, `pyproject.toml`, `.env.example`, `tests/`, `scripts/`, `create_test_repo.sh`, `test-repo/`
(`src/` is out of scope — covered by a separate auditor. No existing file was modified.)
Date: 2026-09-29

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 0 |
| HIGH     | 1 |
| MEDIUM   | 4 |
| LOW      | 4 |
| INFO     | 1 |
| **Total**| **10** |

No real (non-placeholder) hardcoded secrets were found in any in-scope file. The most significant issues are a machine-specific absolute path driving an `rm -rf` in `create_test_repo.sh`, an end-to-end script that hits the real network with live credentials, and pinned test-repo dependencies that carry known CVEs.

---

## Findings

### DEP-01 — HIGH
- **File:** `create_test_repo.sh:4` (and `:6`)
- **Description:** A machine-specific absolute Windows path is hard-coded and then used as the target of `rm -rf`. The path `D:/projects/opd/test-repo` is specific to one machine (note: it is *not* the audit workspace `D:/orca/workspaces/opd/audit/test-repo`). Running this script on a host where that path already contains unrelated data will delete it; on a host without that path it silently creates it. Non-portable (Windows-only) and a latent data-loss vector.
- **Evidence:**
  ```
  4:REPO_DIR="D:/projects/opd/test-repo"
  6:rm -rf "$REPO_DIR"
  ```
- **Recommended fix:** Derive the path from the script's own location (e.g. `REPO_DIR="$(cd "$(dirname "$0")" && pwd)/test-repo"`) or accept it as an argument with a safe default. Guard the `rm -rf` so it only removes a directory that is a known git/test repo (e.g. require a sentinel file, or confirm the path is under the project root).

### DEP-02 — MEDIUM
- **File:** `create_test_repo.sh:2`
- **Description:** The script sets `set -e` but not `set -u` (nor `set -o pipefail`). Unset/typo'd variables expand to empty strings silently, which can turn into unexpected empty arguments (e.g. an empty `git commit -m ""`). There is also no `trap` to clean up a partially-created repo if the script fails mid-way, leaving a stale partial `REPO_DIR`.
- **Evidence:**
  ```
  2:set -e
  ```
- **Recommended fix:** Use `set -euo pipefail` and add a `trap` for cleanup/abort on error.

### DEP-03 — MEDIUM
- **File:** `scripts/test_e2e.py:63,82-84,97-98`
- **Description:** This "test" is not hermetic: it reads **live** configuration (real `OPENAI_API_KEY`, `GITHUB_TOKEN`, `TELEGRAM_BOT_TOKEN`, `OPENAI_BASE_URL`) via `get_settings()`, starts a **real** in-process uvicorn server on `127.0.0.1:8000`, and POSTs a webhook that triggers **real** OpenAI API calls and a **real** Telegram notification. It depends on external network + real credentials and a fixed local port; it will fail or leak side effects (Telegram message, API cost) when run in CI or on a machine without those env vars.
- **Evidence:**
  ```
  63:        settings = get_settings()
  82:    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
  97:        async with httpx.AsyncClient(timeout=60.0) as client:
  98:            response = await client.post(WEBHOOK_URL, content=body, headers=headers)
  ...
  109:        print("[7/8] Done. Check your Telegram for the notification.")
  ```
- **Recommended fix:** Make it opt-in (skip unless an env flag like `PR_SENTINEL_E2E=1` is set), inject dummy/secret-keyed credentials, mock the OpenAI + Telegram clients, and pick an ephemeral port. Keep it out of the default `pytest` run.

### DEP-04 — LOW
- **File:** `test-repo/tests/test_main.py:4`, `test-repo/tests/test_api.py:4`, `test-repo/tests/test_models.py:4`
- **Description:** All three test-repo unit tests are tautological (`assert True`). They would pass even if the module under test were deleted or the function were missing entirely, so they give false confidence and assert nothing about behavior.
- **Evidence:**
  ```
  test_main.py:3  def test_main():
  test_main.py:4      assert True
  ```
- **Recommended fix:** Replace with real assertions against the functions under test (e.g. call `get_endpoint()` and assert its return value).

### DEP-05 — MEDIUM
- **File:** `test-repo/requirements.txt:1-5`
- **Description:** The test-repo pins several package versions that are old and/or carry known CVEs:
  - `flask==2.3.0` — affected by **CVE-2023-30168** and **CVE-2023-32309** (session-cookie caching); fixed in 2.3.2. Current major is 3.x.
  - `requests==2.31.0` — affected by **CVE-2024-4708** (leak of `Proxy-Authorization` header on cross-origin redirect when `verify=False`); fixed in 2.32.0.
  - `jinja2==3.1.2` — affected by **CVE-2024-34057** (ReDoS in `urlize` filter; fixed in 3.1.3) and **CVE-2024-56704** (`xmlattr` filter; fixed in 3.1.6).
  - `sqlalchemy==2.0.0` — early 2.0.0 release, now far behind current 2.0.x (no major CVE at this exact version, but stale).
  - `pytest==7.4.0` — old (no major CVE, but well behind 8.x).
- **Evidence:**
  ```
  1:flask==2.3.0
  2:sqlalchemy==2.0.0
  3:pytest==7.4.0
  4:requests==2.31.0
  5:jinja2==3.1.2
  ```
- **Recommended fix:** Bump to current supported versions (e.g. `flask>=3.0`, `requests>=2.32`, `jinja2>=3.1.6`, `sqlalchemy>=2.0.30`, `pytest>=8.0`) or at least past the cited CVE fix versions.

### DEP-06 — MEDIUM
- **File:** `pyproject.toml:11-19` (runtime deps), `pyproject.toml:24-29` (dev deps)
- **Description:** Runtime dependencies use lower-bound-only specifiers (`>=`) with **no upper bound**, so a future major/minor release can be pulled in silently (non-reproducible builds). The dev dependencies are **unpinned entirely** (bare names). `starlette` (a FastAPI dependency) is not pinned anywhere and is only constrained indirectly.
- **Evidence:**
  ```
  11:    "aiogram>=3.4",
  12:    "fastapi>=0.100",
  ...
  24:    "pytest",
  25:    "pytest-asyncio",
  ...
  ```
- **Recommended fix:** Add upper bounds (e.g. `aiogram>=3.4,<4`, `fastapi>=0.100,<1`) and pin the dev tooling, or generate a lock file (`pip-tools`/`uv`/`poetry`) for reproducible installs.

### DEP-07 — LOW
- **File:** `requirements.txt:1-2`
- **Description:** The root `requirements.txt` is incomplete/inconsistent with `pyproject.toml`. It lists only `openai>=1.0` and `aiosqlite>=0.19`, omitting `aiogram`, `fastapi`, `uvicorn`, `httpx`, `PyGithub`, `pydantic`, and `pydantic-settings` that `pyproject.toml` declares. Installing from `requirements.txt` alone yields a non-runnable app.
- **Evidence:**
  ```
  1:openai>=1.0
  2:aiosqlite>=0.19
  ```
- **Recommended fix:** Keep a single source of truth (prefer `pyproject.toml`) and generate `requirements.txt` from it, or align the two.

### DEP-08 — LOW
- **File:** `scripts/test_graph_only.py:58-61`
- **Description:** The temporary SQLite DB is created with `NamedTemporaryFile(delete=False)` and removed in a `finally` block, but the removal swallows all `OSError`s (`except OSError: pass`). A failed unlink (e.g. file still locked by the OS) leaves a stray temp file behind with no signal.
- **Evidence:**
  ```
  35:    tmp = tempfile.NamedTemporaryFile(prefix="expertise_", suffix=".db", delete=False)
  ...
  59:            os.unlink(db_path)
  60:        except OSError:
  61:            pass
  ```
- **Recommended fix:** Log the unlink failure (or use a `tempfile.TemporaryDirectory` / `tempfile.mkstemp` context manager) so a leak is visible.

### DEP-09 — LOW
- **File:** `test-repo/src/main.py:23` (and `test-repo/src/database.py:46`)
- **Description:** Broad `except Exception` clauses that can hide failures. In `main.py` the `handle_exceptions` decorator catches any exception, logs it, and returns `None`, so the caller cannot distinguish a real error from a normal `None` result. In `database.py` `retry_query` uses a bare `except Exception:` (it does re-raise on the final attempt, so it is less severe).
- **Evidence:**
  ```
  main.py:23        except Exception as e:
  main.py:24            log(f"Error: {e}")
  main.py:25            return None
  database.py:46        except Exception:
  ```
- **Recommended fix:** Catch the specific expected exception types, and in the decorator consider re-raising (or returning a distinct sentinel) rather than collapsing all errors to `None`.

### DEP-10 — INFO
- **File:** `.env.example:1-3`; `tests/test_webhook.py:23,71,73`; `tests/test_bot.py:11,18`; `scripts/test_e2e.py` (env-driven)
- **Description:** No real (non-placeholder) hardcoded secrets were found in any in-scope file. All token/key-looking values are obviously placeholders:
  - `.env.example` uses `your-...` placeholders for `TELEGRAM_BOT_TOKEN`, `GITHUB_TOKEN`, `OPENAI_API_KEY`.
  - `tests/test_webhook.py` uses `GITHUB_SECRET = "test-github-secret"`, `"telegram_bot_token": "123:TEST"`, `"openai_api_key": "test-openai-key"`.
  - `tests/test_bot.py` uses `PRBot("123:TEST")`.
  - `scripts/test_e2e.py` reads credentials from the environment (never prints the values, only `<set>`/`<missing>`).
  - No `sk-…`, `ghp_…`, `github_pat_…`, or `xoxb-…` patterns matched anywhere in scope.
- **Recommended fix:** None required. Keep placeholder convention; ensure a real `.env` is in `.gitignore` (it is, per the root `.gitignore`, which is out of scope here).

---

## `.env.example` variable inventory (item 2)

| Variable | Example value | Looks like a real secret? |
|----------|---------------|---------------------------|
| `TELEGRAM_BOT_TOKEN` | `your-telegram-bot-token` | No — placeholder |
| `GITHUB_TOKEN` | `your-github-token` | No — placeholder |
| `OPENAI_API_KEY` | `your-openai-api-key` | No — placeholder |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | No — default endpoint |
| `OPENAI_MODEL` | `gpt-4o` | No — model name |
| `DATABASE_PATH` | `pr_sentinel.db` | No — relative path |
| `GITHUB_REPO` | `owner/repo` | No — placeholder |
| `TELEGRAM_CHAT_ID` | `123456789` | No — placeholder number |
| `NOTIFICATION_LANGUAGE` | `ru` | No — language code |

No example value in `.env.example` looks like a real secret.

---

## Dependency table (item 3)

| Package | Version specifier | Source file | Note |
|---------|-------------------|-------------|------|
| openai | `>=1.0` | `requirements.txt:1` | Unpinned upper bound (floating) |
| aiosqlite | `>=0.19` | `requirements.txt:2` | Unpinned upper bound (floating) |
| aiogram | `>=3.4` | `pyproject.toml:11` | Unpinned upper bound (floating); no known CVE at 3.4 |
| fastapi | `>=0.100` | `pyproject.toml:12` | Unpinned upper bound (floating) |
| uvicorn | `>=0.23` | `pyproject.toml:13` | Unpinned upper bound (floating) |
| httpx | `>=0.24` | `pyproject.toml:14` | Unpinned upper bound (floating) |
| openai | `>=1.0` | `pyproject.toml:15` | Unpinned upper bound (floating) |
| PyGithub | `>=2.0` | `pyproject.toml:16` | Unpinned upper bound (floating) |
| aiosqlite | `>=0.19` | `pyproject.toml:17` | Unpinned upper bound (floating) |
| pydantic | `>=2.0` | `pyproject.toml:18` | Unpinned upper bound (floating) |
| pydantic-settings | `>=2.0` | `pyproject.toml:19` | Unpinned upper bound (floating) |
| starlette | *(not listed)* | — | Indirect via FastAPI; not pinned anywhere |
| pytest | *(unpinned)* | `pyproject.toml:24` | Dev dep, fully unpinned |
| pytest-asyncio | *(unpinned)* | `pyproject.toml:25` | Dev dep, fully unpinned |
| pytest-cov | *(unpinned)* | `pyproject.toml:26` | Dev dep, fully unpinned |
| ruff | *(unpinned)* | `pyproject.toml:27` | Dev dep, fully unpinned |
| mypy | *(unpinned)* | `pyproject.toml:28` | Dev dep, fully unpinned |
| black | *(unpinned)* | `pyproject.toml:29` | Dev dep, fully unpinned |
| flask | `==2.3.0` | `test-repo/requirements.txt:1` | **Known CVEs:** CVE-2023-30168, CVE-2023-32309; old (3.x current) |
| sqlalchemy | `==2.0.0` | `test-repo/requirements.txt:2` | Old (2.0.x current); no major CVE at this exact version |
| pytest | `==7.4.0` | `test-repo/requirements.txt:3` | Old (8.x current); no major CVE |
| requests | `==2.31.0` | `test-repo/requirements.txt:4` | **Known CVE:** CVE-2024-4708 (fixed 2.32.0) |
| jinja2 | `==3.1.2` | `test-repo/requirements.txt:5` | **Known CVEs:** CVE-2024-34057 (3.1.3), CVE-2024-56704 (3.1.6) |

**Pinning status:** The main project has **no upper bounds** on any runtime dependency and **no pins at all** on dev dependencies (everything floats). Only `test-repo/requirements.txt` uses exact `==` pins — and several of those exact pins carry known CVEs.

---

## Shell script safety — `create_test_repo.sh` (item 4)

| Check | Result |
|-------|--------|
| `set -e` | ✅ Present (line 2) |
| `set -u` / `set -o pipefail` | ❌ Missing (line 2) — see DEP-02 |
| Unquoted variables | ✅ All variable expansions are quoted (`"$REPO_DIR"`, `"$1"`–`"$4"`, `"$msg"`) |
| `eval` | ✅ Not used |
| `curl \| sh` | ✅ Not used |
| `sudo` | ✅ Not used |
| `rm -rf` with variable target | ⚠️ `rm -rf "$REPO_DIR"` (line 6) — quoted (safe from word-splitting) but the target is a **hard-coded machine-specific absolute path** — see DEP-01 |
| `mktemp` usage | ✅ Not used (files created via `cat >` / `echo >>`) |
| Command-substitution pitfalls | ✅ None (no `$(...)` that feeds a shell command) |
| Heredocs / `echo` | ✅ Quoted heredoc delimiters (`<< 'EOF'`) and single-quoted `echo '...'` — no unintended expansion |
| Cleanup on failure | ❌ No `trap`; a mid-script failure leaves a partial `REPO_DIR` — see DEP-02 |
| `git init -b main` (line 9) | ⚠️ Requires Git ≥ 2.28; will fail on older Git |

---

## Test hygiene (item 5)

| Test / script | Network? | Secrets in fixtures? | Temp cleanup? | Notable issue |
|---------------|----------|----------------------|---------------|---------------|
| `tests/test_bot.py` | No (mocked `send_message`) | Placeholder `123:TEST` | n/a | OK |
| `tests/test_database.py` | No | No | `tmp_path` (pytest) | OK; asserts on private `_conn` |
| `tests/test_composer.py` | No | No | n/a | OK (pure formatting) |
| `tests/test_expertise.py` | No (local `git` subprocess) | No (dummy author emails) | `tmp_path` (pytest) | OK; asserts on private `_conn` |
| `tests/test_risk.py` | No (mocked `openai` client, patched `asyncio.sleep`) | No | n/a | OK (well-mocked) |
| `tests/test_webhook.py` | No (in-process `ASGITransport`, mocked GitHub + `analyze_pr`) | Placeholders (`test-github-secret`, `123:TEST`, `test-openai-key`) | `tmp_path` (pytest) | OK; signature-check test (`test_webhook_invalid_signature` → 401) is a real control test |
| `scripts/test_e2e.py` | **Yes — real network** (live uvicorn :8000 + real OpenAI + real Telegram) | Live creds from env | Server shut down in `finally` | **DEP-03** |
| `scripts/test_graph_only.py` | No | No | `NamedTemporaryFile(delete=False)` + `unlink` in `finally` | **DEP-08** (swallowed `OSError`) |
| `test-repo/tests/test_*.py` | No | No | n/a | **DEP-04** (tautological `assert True`) |

**Bare/broad `except` that can hide failures:** `test-repo/src/main.py:23` (`except Exception` → returns `None`) and `test-repo/src/database.py:46` (`except Exception:`) — see DEP-09. `scripts/test_graph_only.py:60` (`except OSError: pass`) — see DEP-08.

**Tests that would pass even if the control were missing:** the three `test-repo/tests/*` files (`assert True`) — see DEP-04. The `tests/` suite, by contrast, contains genuine control tests (e.g. webhook signature → 401, action filter → 400).

---

## Absolute / machine-specific paths (item 6)

| File:line | Path | Note |
|-----------|------|------|
| `create_test_repo.sh:4` | `D:/projects/opd/test-repo` | **Machine-specific Windows absolute path** (and the `rm -rf` target) — DEP-01 |

All other path references are derived portably from `__file__` (e.g. `scripts/test_graph_only.py:22`, `scripts/test_e2e.py:29` → `PROJECT_ROOT = Path(__file__).resolve().parent.parent`) or are relative (`.env.example:6` `DATABASE_PATH=pr_sentinel.db`). No other in-scope file leaks a machine-specific absolute path.
