# Security Audit — PR Sentinel (Consolidated)

- **Date:** 2026-09-29
- **Scope:** Whole repo: `src/`, `tests/`, `scripts/`, `test-repo/`, and config/dependency manifests (`requirements.txt`, `pyproject.toml`, `.env.example`, `create_test_repo.sh`). Consolidated from `security-audit-src.md` (src/) and `security-audit-deps.md` (tests/scripts/config/deps). Findings renumbered SEC-01…SEC-24; content is verbatim from the two source reports. No files were modified.

---

## Executive Summary

**Overall risk: MODERATE.** The runtime service (`src/`) has a sound core security posture — timing-safe HMAC-SHA256 webhook verification over the raw body, all-parameterized SQL, argument-list git subprocess (no shell), outbound HTTP with timeouts and TLS verification, strictly parsed/degraded LLM output, and complete MarkdownV2 escaping of Telegram content. There are no CRITICAL findings. The residual risk is concentrated in (a) availability/cost weaknesses on the public webhook (no dedicated secret, unbounded body, no rate limiting/queue, no replay protection) and (b) test/dev tooling: a machine-specific absolute path driving `rm -rf` in `create_test_repo.sh` (the single HIGH), a non-hermetic end-to-end script that hits the real network with live credentials, and pinned test-repo dependencies carrying known CVEs.

**Total findings (both reports combined): 24**

| Severity | Open count |
|----------|-------|
| CRITICAL | 0 |
| HIGH     | 0 |
| MEDIUM   | 1 |
| LOW      | 11 |
| INFO     | 5 |
| **Total**| **17** |
7 findings fixed on 2026-09-29 (SEC-01…SEC-05, SEC-07, SEC-08)

**Top 3 most important issues:**

**1. (SEC-01, HIGH) Machine-specific absolute path drives `rm -rf` in `create_test_repo.sh`.** The script hard-codes `REPO_DIR="D:/projects/opd/test-repo"` — a path specific to one machine (and *not* the audit workspace) — and then runs `rm -rf "$REPO_DIR"`. On a host where that path already holds unrelated data, running the script deletes it; on a host without it, the path is silently created. It is the only HIGH finding, is non-portable (Windows-only), and is a latent data-loss vector.

**2. (SEC-02, MEDIUM) The webhook HMAC secret is the GitHub API token — no dedicated secret.** `X-Hub-Signature-256` verification uses `settings.github_token` as the HMAC key, the same credential used as the live `Authorization: Bearer` GitHub API token. GitHub recommends a separate random webhook secret; reusing the API token removes defense-in-depth, so any leak of the token (CI logs, env dump, a future logging regression) immediately allows forging webhook deliveries, which triggers paid LLM calls and messages into the Telegram chat.

**3. (SEC-04, MEDIUM) No rate limiting, concurrency bound, or queue on the webhook; long inline processing.** Each `/webhook/github` request performs, inline and sequentially, 2 GitHub API calls (30 s timeout each) + an OpenAI call (up to 4 attempts × 30 s + backoff ≈ 150 s worst case) + a Telegram send, and only then returns 200. With no rate limiting, concurrency limit, or background queue, a sender holding a valid signature can open many concurrent deliveries (each consuming OpenAI quota and GitHub rate limit), and because the response routinely exceeds GitHub's delivery timeout, GitHub redelivers — multiplying LLM calls and Telegram duplicates for the same PR.

---

## Findings

### HIGH

#### SEC-01 — Machine-specific absolute path drives `rm -rf` (`create_test_repo.sh`)
- **File:** `create_test_repo.sh:4` (and `:6`)
- **Status:** FIXED (2026-09-29) — REPO_DIR now derived from script location with optional $1 override; set -euo pipefail + cleanup trap
- **Description:** A machine-specific absolute Windows path is hard-coded and then used as the target of `rm -rf`. The path `D:/projects/opd/test-repo` is specific to one machine (note: it is *not* the audit workspace `D:/orca/workspaces/opd/audit/test-repo`). Running this script on a host where that path already contains unrelated data will delete it; on a host without that path it silently creates it. Non-portable (Windows-only) and a latent data-loss vector.
- **Evidence:**
  ```
  4:REPO_DIR="D:/projects/opd/test-repo"
  6:rm -rf "$REPO_DIR"
  ```
- **Recommended fix:** Derive the path from the script's own location (e.g. `REPO_DIR="$(cd "$(dirname "$0")" && pwd)/test-repo"`) or accept it as an argument with a safe default. Guard the `rm -rf` so it only removes a directory that is a known git/test repo (e.g. require a sentinel file, or confirm the path is under the project root).

### MEDIUM

#### SEC-02 — Webhook HMAC secret is the GitHub API token (no dedicated secret)
- **Severity:** MEDIUM
- **Status:** FIXED (2026-09-29) — added required `github_webhook_secret` setting (`GITHUB_WEBHOOK_SECRET` in `.env.example`); `X-Hub-Signature-256` is now verified against that secret, `github_token` only for the GitHub API Bearer header
- **Category:** Webhook security / Secrets management
- **File:line:** `src/webhook/server.py:173-174` (secret passed), `src/config.py:8` (no `github_webhook_secret` field), `.env.example:2`
- **Description:** The `X-Hub-Signature-256` verification uses `settings.github_token` as the HMAC key. The same credential is the live GitHub API token (used as `Authorization: Bearer` in `src/webhook/server.py:89`). GitHub recommends a separate, random webhook secret; reusing the API token removes defense-in-depth: any leak of the token (CI logs, env dump, a future logging regression) immediately allows forging webhook deliveries, which triggers paid LLM calls and messages into the Telegram chat.
- **Evidence:**
  ```python
  # src/webhook/server.py:173-174
  if not _verify_signature(
      body, request.headers.get("X-Hub-Signature-256"), deps.settings.github_token
  ):
  ```
- **Recommended fix:** Add a `github_webhook_secret: str` settings field (no default) and the matching `GITHUB_WEBHOOK_SECRET` in `.env.example`; use it only for HMAC verification.

#### SEC-03 — Unbounded request body read before authentication
- **Severity:** MEDIUM
- **Status:** FIXED (2026-09-29) — handler rejects `Content-Length` above `MAX_BODY_BYTES` (5 MiB) with 413 before reading the body
- **Category:** DoS / resource
- **File:line:** `src/webhook/server.py:167`
- **Description:** `body = await request.body()` buffers the entire request body with no size limit, and it happens **before** the event-type check (line 169) and signature verification (line 173). An unauthenticated sender can therefore force the server to allocate arbitrarily large in-memory buffers (e.g., a 200 MB body per request, concurrently), which are only rejected afterwards with 401.
- **Evidence:**
  ```python
  # src/webhook/server.py:167-176
  body = await request.body()

  if request.headers.get("X-GitHub-Event") != "pull_request":
      raise HTTPException(status_code=400, ...)
  if not _verify_signature(body, request.headers.get("X-Hub-Signature-256"), deps.settings.github_token):
      raise HTTPException(status_code=401, detail="invalid X-Hub-Signature-256")
  ```
- **Recommended fix:** Enforce a cap before reading (e.g., check `Content-Length` against a limit such as 5 MiB and return 413, or stream-read with a byte budget). GitHub `pull_request` payloads are typically well under 1 MB.

#### SEC-04 — No rate limiting, concurrency bound, or queue on the webhook; long inline processing
- **Severity:** MEDIUM
- **Status:** FIXED (2026-09-29) — handler returns 202 immediately and enqueues on a bounded `asyncio.Queue` (maxsize 100) consumed by a single background worker; full queue returns 503
- **Category:** DoS / resource / Webhook security
- **File:line:** `src/webhook/server.py:162-232` (handler), `src/webhook/server.py:205-221` (GitHub fetch + LLM), `src/analyzer/risk.py:160-202` (retry loop)
- **Description:** Each `/webhook/github` request performs, inline and sequentially: 2 GitHub API calls (30 s timeout each) + an OpenAI call (up to 4 attempts × 30 s timeout + 2/4/8 s backoff ≈ 150 s worst case) + a Telegram send, and only then returns 200. There is no rate limiting, no concurrency limit, and no background queue. Consequences: (a) a sender holding a valid signature (see SEC-02) can open many concurrent deliveries, each consuming OpenAI quota (direct cost) and GitHub API rate limit; (b) because the response routinely exceeds GitHub's delivery timeout, GitHub marks deliveries failed and redelivers them, multiplying LLM calls and Telegram duplicates for the same PR.
- **Evidence:**
  ```python
  # src/webhook/server.py:205-227
  diff, files = await _fetch_pr_diff_and_files(deps.github, deps.settings.github_repo, pr_number)
  ...
  risk = await analyze_pr(diff=diff, pr_title=title, ...)
  reviewers = await deps.graph.recommend_reviewers(files)
  ...
  await deps.bot.send_notification(deps.settings.telegram_chat_id, message)
  ```
  ```python
  # src/analyzer/risk.py:160
  for attempt in range(MAX_RETRIES + 1):  # 4 attempts, 30 s timeout each + backoff
  ```
- **Recommended fix:** Return 202 quickly and process the delivery in a bounded worker queue (e.g., `asyncio.Semaphore` / queue with a max size); add per-endpoint rate limiting (e.g., by `X-GitHub-Delivery` or source IP); deduplicate by delivery id (see SEC-09).

#### SEC-05 — `create_test_repo.sh` sets `set -e` but not `set -u`/`pipefail`; no `trap` cleanup
- **File:** `create_test_repo.sh:2`
- **Status:** FIXED (2026-09-29) — script now uses `set -euo pipefail` and a `trap` that removes `REPO_DIR` on ERR/INT/TERM
- **Description:** The script sets `set -e` but not `set -u` (nor `set -o pipefail`). Unset/typo'd variables expand to empty strings silently, which can turn into unexpected empty arguments (e.g. an empty `git commit -m ""`). There is also no `trap` to clean up a partially-created repo if the script fails mid-way, leaving a stale partial `REPO_DIR`.
- **Evidence:**
  ```
  2:set -e
  ```
- **Recommended fix:** Use `set -euo pipefail` and add a `trap` for cleanup/abort on error.

#### SEC-06 — End-to-end script is not hermetic (real network + live credentials)
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

#### SEC-07 — Test-repo pins old/CVE-affected package versions
- **File:** `test-repo/requirements.txt:1-5`
- **Status:** FIXED (2026-09-29) — `test-repo/requirements.txt` bumped past the cited CVE fix versions (`flask>=3.0`, `sqlalchemy>=2.0.30`, `pytest>=8.0`, `requests>=2.32`, `jinja2>=3.1.6`)
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

#### SEC-08 — Runtime deps have no upper bounds; dev deps fully unpinned
- **File:** `pyproject.toml:11-19` (runtime deps), `pyproject.toml:24-29` (dev deps)
- **Status:** FIXED (2026-09-29) — upper bounds added to all runtime deps in `pyproject.toml` (e.g. `aiogram>=3.4,<4`) and dev deps are now bounded
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

### LOW

#### SEC-09 — No replay protection (no `X-GitHub-Delivery` deduplication)
- **Severity:** LOW
- **Category:** Webhook security
- **File:line:** `src/webhook/server.py:162-232` (handler ignores `X-GitHub-Delivery`)
- **Description:** Deliveries are verified by signature only. A captured (or GitHub-redelivered) delivery can be replayed at any time, re-triggering the full pipeline: duplicate Telegram notification, extra LLM cost, and a new `pr_history` row.
- **Evidence:** no reference to `X-GitHub-Delivery` anywhere in `src/webhook/server.py`.
- **Recommended fix:** Keep a short-lived (e.g., 24 h) seen-set of `X-GitHub-Delivery` ids (in SQLite) and skip duplicates.

#### SEC-10 — Unbounded LLM reason length can exceed Telegram's 4096-char limit; failure surfaces as 500 + redelivery
- **Severity:** LOW
- **Category:** Input validation / DoS
- **File:line:** `src/analyzer/risk.py:102` (reasons truncated to 2 items, no per-item length cap), `src/notifications/composer.py:112`, `src/bot/bot.py:56`, `src/webhook/server.py:227`
- **Description:** The parser caps `reasons` at two entries but not their length (`[str(r).strip() for r in raw_reasons if str(r).strip()][:2]`). A long LLM reply (its content is influenced by PR title/body/diff, i.e., PR-author-controlled) can produce a message longer than Telegram's 4096-character limit; `send_message(..., parse_mode="MarkdownV2")` then raises, the unhandled exception turns into a 500, and GitHub redelivers — repeating the failure.
- **Evidence:**
  ```python
  # src/analyzer/risk.py:102
  reasons = [str(r).strip() for r in raw_reasons if str(r).strip()][:2]
  ```
  ```python
  # src/bot/bot.py:56
  await self.bot.send_message(chat_id, text, parse_mode="MarkdownV2")
  ```
- **Recommended fix:** Cap each reason (e.g., 200 chars) in `_parse_assessment`, and/or wrap `send_notification` in a try/except that logs and degrades instead of failing the delivery.

#### SEC-11 — `pr_history` grows unbounded; no uniqueness on `pr_number`
- **Severity:** LOW
- **Category:** DoS / resource (SQLite growth)
- **File:line:** `src/db/database.py:12-21` (schema), `src/webhook/server.py:228-230` (insert)
- **Description:** Every processed delivery (including each `synchronize` of a long-lived PR) inserts a new row; there is no unique constraint on `pr_number`, no retention/cleanup, and no index beyond the implicit rowid. The table grows monotonically for the life of the service.
- **Evidence:**
  ```python
  # src/db/database.py:13-20
  CREATE TABLE IF NOT EXISTS pr_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      pr_number INTEGER,
      ...
  ```
  ```python
  # src/webhook/server.py:228-230
  await deps.db.record_pr(pr_number, title, url, risk.level, datetime.now(UTC).isoformat())
  ```
- **Recommended fix:** Add a unique index on `pr_number` (upsert latest assessment per PR) and/or periodic pruning of old rows.

#### SEC-12 — Swagger UI and OpenAPI schema exposed on the public webhook server
- **Severity:** LOW
- **Category:** Misc (debug/info disclosure)
- **File:line:** `src/webhook/server.py:155`
- **Description:** `FastAPI(title="PR Sentinel", lifespan=lifespan)` leaves the default `docs_url=/docs` and `openapi_url=/openapi.json` enabled on the internet-facing webhook server (bound to `0.0.0.0` by default, `src/main.py:91`). Anyone who can reach the port gets an interactive API description of the endpoints.
- **Evidence:**
  ```python
  # src/webhook/server.py:155
  app = FastAPI(title="PR Sentinel", lifespan=lifespan)
  ```
- **Recommended fix:** In production, set `docs_url=None, redoc_url=None, openapi_url=None` (or gate them behind an auth dependency).

#### SEC-13 — Telegram bot has no access control
- **Severity:** LOW
- **Category:** Telegram bot
- **File:line:** `src/bot/bot.py:24`, `src/bot/bot.py:26-28`
- **Description:** The only handler is `/start`, registered for all messages with no chat/user whitelist; anyone who discovers the bot's username can converse with it ("PR Sentinel is active."). The impact today is minor (no data is exposed), but there is no allowlist for when handlers are added.
- **Evidence:**
  ```python
  # src/bot/bot.py:24, 26-28
  self.dp.message.register(self._on_start)

  async def _on_start(self, message: Message) -> None:
      await message.answer("PR Sentinel is active.")
  ```
- **Recommended fix:** Restrict handlers to the configured `telegram_chat_id` (or a user-id allowlist) via a dispatcher filter/middleware.

#### SEC-14 — Attacker-influenced LLM output written to logs with embedded newlines (log injection)
- **Severity:** LOW
- **Category:** Misc (logging)
- **File:line:** `src/analyzer/risk.py:87`, `src/analyzer/risk.py:91`, `src/analyzer/risk.py:96`
- **Description:** On malformed LLM replies, the raw model output (up to 200 chars) is logged verbatim. The LLM's reply is shaped by PR title/body/diff, which the PR author controls; a reply containing newlines can forge log lines (e.g., one that looks like `INFO ... notified about PR 999 (risk=LOW)`), complicating incident response.
- **Evidence:**
  ```python
  # src/analyzer/risk.py:87
  logger.warning("Malformed JSON in LLM response: %.200s", raw)
  ```
- **Recommended fix:** Log a newline-stripped/escaped representation (e.g., `raw.replace("\n", "\\n")`) or a hash + length instead of the raw text.

#### SEC-15 — `pull_request.body` not type-validated, unlike the other PR fields
- **Severity:** LOW
- **Category:** Input validation
- **File:line:** `src/webhook/server.py:216`, `src/analyzer/risk.py:64-65`
- **Description:** `number`, `title`, and `html_url` are strictly type-checked (including a `bool`-is-not-`int` guard, `src/webhook/server.py:195-203`), but `body` is passed through as `pr.get("body") or ""`. A non-string JSON value (number, list, object) is accepted and stringified into the LLM prompt. There is no code-injection impact (it only reaches an f-string prompt), but it is an inconsistency in the validation boundary and lets arbitrarily large non-text bodies into the prompt path.
- **Evidence:**
  ```python
  # src/webhook/server.py:216
  pr_body=pr.get("body") or "",
  ```
  ```python
  # src/analyzer/risk.py:65
  parts.append(f"PR description:\n{pr_body}")
  ```
- **Recommended fix:** Validate `isinstance(pr.get("body"), str)` (defaulting to `""`) alongside the other field checks, and optionally cap body length before prompt assembly.

#### SEC-16 — Test-repo unit tests are tautological (`assert True`)
- **File:** `test-repo/tests/test_main.py:4`, `test-repo/tests/test_api.py:4`, `test-repo/tests/test_models.py:4`
- **Description:** All three test-repo unit tests are tautological (`assert True`). They would pass even if the module under test were deleted or the function were missing entirely, so they give false confidence and assert nothing about behavior.
- **Evidence:**
  ```
  test_main.py:3  def test_main():
  test_main.py:4      assert True
  ```
- **Recommended fix:** Replace with real assertions against the functions under test (e.g. call `get_endpoint()` and assert its return value).

#### SEC-17 — Root `requirements.txt` is incomplete/inconsistent with `pyproject.toml`
- **File:** `requirements.txt:1-2`
- **Description:** The root `requirements.txt` is incomplete/inconsistent with `pyproject.toml`. It lists only `openai>=1.0` and `aiosqlite>=0.19`, omitting `aiogram`, `fastapi`, `uvicorn`, `httpx`, `PyGithub`, `pydantic`, and `pydantic-settings` that `pyproject.toml` declares. Installing from `requirements.txt` alone yields a non-runnable app.
- **Evidence:**
  ```
  1:openai>=1.0
  2:aiosqlite>=0.19
  ```
- **Recommended fix:** Keep a single source of truth (prefer `pyproject.toml`) and generate `requirements.txt` from it, or align the two.

#### SEC-18 — Temp DB unlink swallows `OSError` (`test_graph_only.py`)
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

#### SEC-19 — Broad `except Exception` clauses that can hide failures (test-repo)
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

### INFO

#### SEC-20 — OpenAI `base_url` is operator-supplied; a non-TLS base URL would send the API key in cleartext
- **Severity:** INFO
- **Category:** External API calls
- **File:line:** `src/config.py:10`, `src/analyzer/risk.py:146`, `.env.example:4`
- **Description:** `openai.AsyncOpenAI(timeout=..., api_key=..., base_url=base_url)` uses the configured base URL verbatim. The example value is HTTPS, but nothing validates the scheme; if an operator sets `OPENAI_BASE_URL=http://...` (e.g., a local proxy), the `OPENAI_API_KEY` travels unencrypted.
- **Evidence:**
  ```python
  # src/analyzer/risk.py:146
  client = openai.AsyncOpenAI(timeout=REQUEST_TIMEOUT, api_key=api_key, base_url=base_url)
  ```
- **Recommended fix:** Validate that `openai_base_url`, when set, starts with `https://`.

#### SEC-21 — asyncio task failures are only observed at shutdown
- **Severity:** INFO
- **Category:** Misc (asyncio task handling)
- **File:line:** `src/main.py:101-112`
- **Description:** `bot_task`/`server_task` are created and their exceptions are only inspected after `stop_event` fires (`asyncio.gather(..., return_exceptions=True)` at line 109). If `bot.start()` raises early (e.g., invalid Telegram token), the bot is silently down for the entire process lifetime; the error is logged only at shutdown. No "exception was never retrieved" leak occurs, but early failure is invisible while running.
- **Evidence:**
  ```python
  # src/main.py:101-102, 109-112
  bot_task = asyncio.create_task(bot.start(), name="telegram-bot")
  server_task = asyncio.create_task(server.serve(), name="webhook-server")
  ...
  results = await asyncio.gather(bot_task, server_task, return_exceptions=True)
  for name, result in zip(...):
      if isinstance(result, BaseException):
          logger.error("%s exited with: %s", name, result)
  ```
- **Recommended fix:** Add a done-callback that logs immediately (and optionally exits the process) when a task fails before shutdown.

#### SEC-22 — Unauthenticated requests get a 400 event-type oracle before the 401; `action` reflected in error detail
- **Severity:** INFO
- **Category:** Webhook security
- **File:line:** `src/webhook/server.py:169-172`, `src/webhook/server.py:185-187`
- **Description:** The `X-GitHub-Event` header is checked before the signature, so an unauthenticated client can distinguish "wrong event" (400) from "bad signature" (401), and the raw (attacker-controlled) `action` value is reflected in the 400 detail (`f"action {action!r} is not processed"`). The response is JSON-serialized by FastAPI, so this is not XSS, and the value is bounded only by the (unbounded, see SEC-03) body. Minor information disclosure.
- **Evidence:**
  ```python
  # src/webhook/server.py:169-172
  if request.headers.get("X-GitHub-Event") != "pull_request":
      raise HTTPException(status_code=400, detail="expected X-GitHub-Event: pull_request")
  ```
- **Recommended fix:** Verify the signature first and return a single generic 401/400 for all unauthenticated rejections.

#### SEC-23 — Dependency manifest inconsistency (context)
- **Severity:** INFO
- **Category:** Dependencies
- **File:line:** `requirements.txt:1-2`, `pyproject.toml:10-20`
- **Description:** `requirements.txt` pins only `openai>=1.0` and `aiosqlite>=0.19`, while `pyproject.toml` declares nine dependencies; `PyGithub` (`pyproject.toml:16`) is never imported in `src/`. No vulnerability itself, but install paths can diverge (e.g., a `pip install -r requirements.txt` environment missing `fastapi`/`aiogram`).
- **Evidence:**
  ```text
  # requirements.txt
  openai>=1.0
  aiosqlite>=0.19
  ```
- **Recommended fix:** Generate `requirements.txt` from the `pyproject.toml` dependency list (and drop `PyGithub` if unused).

#### SEC-24 — No real (non-placeholder) hardcoded secrets in tests/scripts/config
- **File:** `.env.example:1-3`; `tests/test_webhook.py:23,71,73`; `tests/test_bot.py:11,18`; `scripts/test_e2e.py` (env-driven)
- **Description:** No real (non-placeholder) hardcoded secrets were found in any in-scope file. All token/key-looking values are obviously placeholders:
  - `.env.example` uses `your-...` placeholders for `TELEGRAM_BOT_TOKEN`, `GITHUB_TOKEN`, `OPENAI_API_KEY`.
  - `tests/test_webhook.py` uses `GITHUB_SECRET = "test-github-secret"`, `"telegram_bot_token": "123:TEST"`, `"openai_api_key": "test-openai-key"`.
  - `tests/test_bot.py` uses `PRBot("123:TEST")`.
  - `scripts/test_e2e.py` reads credentials from the environment (never prints the values, only `<set>`/`<missing>`).
  - No `sk-…`, `ghp_…`, `github_pat_…`, or `xoxb-…` patterns matched anywhere in scope.
- **Recommended fix:** None required. Keep placeholder convention; ensure a real `.env` is in `.gitignore` (it is, per the root `.gitignore`, which is out of scope here).

---

## Correct controls

The following controls are implemented correctly and should NOT be re-reported:

1. **Timing-safe HMAC-SHA256 webhook verification over the raw body** — `src/webhook/server.py:52-57`: `hmac.new(secret, body, hashlib.sha256)` compared with `hmac.compare_digest`; rejects missing/`sha256=`-prefixed-absent signatures.
2. **Signature checked before payload parsing/use** — `src/webhook/server.py:173-176` precedes `json.loads` (line 178) and any processing; unauthenticated requests never reach the DB, LLM, or Telegram.
3. **Correct event/action filtering** — `X-GitHub-Event == "pull_request"` (`src/webhook/server.py:169`) and `action ∈ {"opened", "synchronize"}` (`src/webhook/server.py:37, 186`).
4. **Strict webhook field validation** — `number` is a non-`bool` int, `title`/`html_url` are strings (`src/webhook/server.py:195-203`); `pr_number` is validated before being interpolated into the GitHub API path (line 206-208).
5. **GitHub file-list entries validated before use** — only `dict` entries with a `str` `filename` are kept (`src/webhook/server.py:73-77`).
6. **All SQL parameterized** — `INSERT ... VALUES (?, ?, ?, ?, ?)` (`src/db/database.py:53-57`), `LIMIT ?` (`src/db/database.py:63-67`), `executemany` with placeholders (`src/graph/expertise.py:184-188`), and the `IN (...)` query builds the f-string from `?` placeholders only, with file paths bound as parameters (`src/graph/expertise.py:249-253`).
7. **No shell in subprocess; input from operator env, not webhook** — `asyncio.create_subprocess_exec("git", "log", ...)` argument-list form, `cwd` from the `REPO_PATH` env var (`src/graph/expertise.py:142-150`, `src/main.py:67`).
8. **Git SHAs validated as 40 hex chars before use as row keys** — `src/graph/expertise.py:26, 216-220`.
9. **Timeouts on all outbound HTTP** — GitHub `httpx.AsyncClient` 30 s (`src/webhook/server.py:36, 88`); OpenAI client 30 s plus an `asyncio.wait_for` 30 s guard (`src/analyzer/risk.py:28, 146, 162-165`); TLS verification is default (no `verify=False` / `ssl=False` anywhere in `src/`).
10. **Bounded retry with exponential backoff** — 429s and timeouts retried with 2 s base / 30 s cap / max 3 retries, then graceful degradation (`src/analyzer/risk.py:160-202`).
11. **Diff truncated before LLM** — `MAX_DIFF_CHARS = 50_000` (`src/analyzer/risk.py:32, 62-63`); PR file paths are used only in SQL, never on the filesystem (no path-traversal surface).
12. **Strict, non-raising LLM output parsing** — malformed JSON / non-object / unknown level all degrade to `HIGH` with confidence 0; level validated against a fixed set; `confidence` clamped to `[0, 1]`; reasons capped at 2 (`src/analyzer/risk.py:70-111`).
13. **Complete MarkdownV2 escaping of all dynamic Telegram content** — title, reasons, reviewer logins, and URL are escaped with a full special-character set including backslash, handled before other specials (`src/notifications/composer.py:11, 14-24, 108, 112, 114, 118`), sent with `parse_mode="MarkdownV2"` (`src/bot/bot.py:56`); chat id is a settings-provided `int` (`src/config.py:14`).
14. **Secrets via required settings, no hardcoded values** — `telegram_bot_token`, `github_token`, `openai_api_key` have no defaults (`src/config.py:7-9`); `.env` loaded via `env_file` with environment variables taking precedence (pydantic-settings default order); no token/key literals anywhere in `src/`.
15. **Secrets not logged** — log statements use lazy %-formatting with PR numbers, risk levels, and exception classes only (e.g., `src/webhook/server.py:210, 231`; `src/analyzer/risk.py:175-197`); HTTP error responses are generic and leak no internals (e.g., `"failed to fetch PR from GitHub"`, `src/webhook/server.py:211`).
16. **No unsafe deserialization** — no `pickle`, `eval`, `exec`, or `shell=True` anywhere in `src/`.
17. **SSRF-safe GitHub calls** — fixed `https://api.github.com` base URL constant; no user-controlled URLs are fetched (`src/webhook/server.py:34, 87-91`).
18. **Idempotent, complete resource cleanup** — github client, graph DB, history DB, and bot session closed on shutdown (`src/webhook/server.py:113-118`, `src/main.py:114-116`); task results collected with `return_exceptions=True` so no "exception never retrieved" leaks (`src/main.py:109`).

---

## Dependency table

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
