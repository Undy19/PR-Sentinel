# Security Audit — PR Sentinel (Consolidated, v2)

- **Date:** 2026-09-29 (re-audit; supersedes the v1 consolidated report of the same date)
- **Scope:** Whole repo: `src/`, `tests/`, `scripts/`, `test-repo/`, `create_test_repo.sh`, and manifests (`requirements.txt`, `pyproject.toml`, `.env.example`).
- **Method:** Manual line-by-line review of every in-scope file against the current source state; each v1 finding re-verified against the code. The OMP-native scanner was unavailable for this run (no stored OAuth credential for the active model), so findings are from direct code review.
- **v1 corrections:** v1's header claimed 24 findings but its open-count table summed to 17 and never marked SEC-09 fixed even though the replay-dedup code it recommends is present. v2 resolves both: SEC-09 is verified FIXED in code; totals below are exact.

---

## Executive Summary

**Overall risk: MODERATE.** The runtime service (`src/`) has a sound core security posture — timing-safe HMAC-SHA256 webhook verification over the raw body using a dedicated secret, all-parameterized SQL, argument-list git subprocess (no shell), outbound HTTP with timeouts and TLS verification, strictly parsed/degraded LLM output, complete MarkdownV2 escaping of Telegram content, bounded request body, bounded worker queue with 503 backpressure, and 24 h replay deduplication. No CRITICAL or HIGH findings. Residual risk is concentrated in (a) test/dev tooling: the e2e script signs with the **old** webhook key (always 401 since the SEC-02 fix) and remains non-hermetic (live credentials, real Telegram side effects); (b) two residual webhook DoS/replay edges (non-atomic dedup race, chunked-body bypass of the size cap); (c) small hygiene items (unbounded `pr_history`, exposed Swagger, bot without allowlist, log injection).

**Totals: 29 findings — 9 fixed (verified in code), 20 open.**

| Severity | Fixed | Open |
|----------|-------|------|
| CRITICAL | 0 | 0 |
| HIGH     | 0 | 0 |
| MEDIUM   | 0 | 2 |
| LOW      | 8 | 13 |
| INFO     | 1 | 5 |
| **Total**| **9** | **20** |

**Top 3 open issues:**

1. **(SEC-25, MEDIUM) `scripts/test_e2e.py:94` signs the webhook with `settings.github_token`, but the server verifies `X-Hub-Signature-256` against `settings.github_webhook_secret`** (the SEC-02 fix). The e2e script was not updated, so it now always receives 401 — the one end-to-end test of the security-critical path no longer exercises it. One-line fix.
2. **(SEC-06, MEDIUM) The e2e script is not hermetic.** It reads live credentials via `get_settings()`, binds a fixed `127.0.0.1:8000`, and a successful run triggers a real OpenAI call (cost) and a real Telegram message. Not safe to run in CI or on machines with real env vars.
3. **(SEC-26, LOW) Replay dedup is check-then-mark, not atomic.** `is_delivery_seen` → `mark_delivery_seen` (`src/webhook/server.py:257-261`) has an `await` between them; two identical deliveries arriving concurrently both pass the check and both run the full paid pipeline (duplicate Telegram message + LLM cost).

---

## Findings

### MEDIUM

#### SEC-06 — End-to-end script is not hermetic (real network + live credentials)
- **Severity:** MEDIUM
- **Status:** OPEN
- **Category:** Test tooling / secrets handling
- **File:line:** `scripts/test_e2e.py:63` (live `get_settings()`), `:36-38` (fixed `127.0.0.1:8000`), `:77-84` (real bundle + uvicorn), `:97-98` (real POST), `:109` (real Telegram side effect)
- **Description:** The "test" reads **live** configuration (real `OPENAI_API_KEY`, `GITHUB_TOKEN`, `GITHUB_WEBHOOK_SECRET`, `TELEGRAM_BOT_TOKEN`, `OPENAI_BASE_URL`), starts a real in-process uvicorn server on a fixed local port, and POSTs a webhook that triggers a real OpenAI API call and a real Telegram notification. Running it in CI or on a machine with real env vars leaks side effects (Telegram message, API cost) and fails without them; the fixed port collides with a locally running instance.
- **Evidence:**
  ```python
  # scripts/test_e2e.py:63, 82, 97-98, 109
  settings = get_settings()
  config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
  ...
  response = await client.post(WEBHOOK_URL, content=body, headers=headers)
  ...
  print("[7/8] Done. Check your Telegram for the notification.")
  ```
- **Recommended fix:** Make it opt-in (skip unless `PR_SENTINEL_E2E=1`), inject dummy credentials, mock the OpenAI + Telegram clients, and use an ephemeral port. Keep it out of the default `pytest` run.

#### SEC-25 — E2E script signs with the old webhook key; always 401 since the SEC-02 fix *(new in v2)*
- **Severity:** MEDIUM
- **Status:** OPEN
- **Category:** Webhook security / test tooling (functional breakage of a security test)
- **File:line:** `scripts/test_e2e.py:94` (signing key), `src/webhook/server.py:248-252` (verification key)
- **Description:** After SEC-02 introduced the dedicated `github_webhook_secret`, the server verifies `X-Hub-Signature-256` against `settings.github_webhook_secret` (`src/webhook/server.py:251`), but the e2e script still computes the signature with `settings.github_token` (`scripts/test_e2e.py:94`). With the two settings at their documented distinct values, every e2e run gets `401 invalid X-Hub-Signature-256`, so the only end-to-end exercise of the signature path is dead. It also illustrates the two-credential split drifting across consumers.
- **Evidence:**
  ```python
  # scripts/test_e2e.py:92-96
  headers = {
      "X-GitHub-Event": "pull_request",
      "X-Hub-Signature-256": _sign(body, settings.github_token),  # ← old key
      "Content-Type": "application/json",
  }
  ```
  ```python
  # src/webhook/server.py:248-252
  if not _verify_signature(
      body,
      request.headers.get("X-Hub-Signature-256"),
      deps.settings.github_webhook_secret,                          # ← new key
  ):
  ```
- **Recommended fix:** Sign with `settings.github_webhook_secret` in `scripts/test_e2e.py:94`.

### LOW

#### SEC-10 — Unbounded LLM reason length can exceed Telegram's 4096-char limit; failure loses the notification
- **Severity:** LOW
- **Status:** OPEN
- **Category:** Input validation / availability
- **File:line:** `src/analyzer/risk.py:102` (no per-item length cap), `src/notifications/composer.py:112`, `src/bot/bot.py:54-56`, `src/webhook/server.py:127-136` (worker catch)
- **Description:** The parser caps `reasons` at two entries but not their length. A long LLM reply (influenced by PR-author-controlled title/body/diff) can produce a message over Telegram's 4096-character limit; `send_message(..., parse_mode="MarkdownV2")` raises, and the worker's broad `except Exception` (`src/webhook/server.py:133-134`) logs and drops the item. Since the webhook already answered 202, GitHub does **not** redeliver — the notification is silently lost (v1's "500 + redelivery" impact no longer applies once the queue landed; the residual is lost notifications, not duplicates).
- **Evidence:**
  ```python
  # src/analyzer/risk.py:102
  reasons = [str(r).strip() for r in raw_reasons if str(r).strip()][:2]
  ```
- **Recommended fix:** Cap each reason (e.g., 200 chars) in `_parse_assessment`; optionally wrap `send_notification` with a degrade-and-log path that still records the PR.

#### SEC-11 — `pr_history` grows unbounded; no uniqueness on `pr_number`
- **Severity:** LOW
- **Status:** OPEN
- **Category:** DoS / resource (SQLite growth)
- **File:line:** `src/db/database.py:13-21` (schema), `src/webhook/server.py:121-123` (insert per delivery)
- **Description:** Every processed delivery (including each `synchronize` of a long-lived PR) inserts a new row; no unique constraint on `pr_number`, no retention/cleanup. The table grows monotonically for the life of the service.
- **Evidence:**
  ```python
  # src/db/database.py:13-21
  CREATE TABLE IF NOT EXISTS pr_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      pr_number INTEGER,
      ...
  ```
- **Recommended fix:** Upsert the latest assessment per `pr_number` (unique index) and/or prune rows older than a retention window.

#### SEC-12 — Swagger UI and OpenAPI schema exposed on the public webhook server
- **Severity:** LOW
- **Status:** OPEN
- **Category:** Info disclosure
- **File:line:** `src/webhook/server.py:221` (`FastAPI(title="PR Sentinel", lifespan=lifespan)` with default `docs_url`/`openapi_url`), `src/main.py:90` (bound to `0.0.0.0` by default)
- **Description:** `/docs` and `/openapi.json` are enabled on the internet-facing webhook server. Anyone who can reach the port gets an interactive description of the endpoints (low sensitivity, but gratuitous).
- **Recommended fix:** `FastAPI(..., docs_url=None, redoc_url=None, openapi_url=None)` in production.

#### SEC-13 — Telegram bot has no access control
- **Severity:** LOW
- **Status:** OPEN
- **Category:** Telegram bot
- **File:line:** `src/bot/bot.py:24, 26-28`
- **Description:** The only handler is `/start`, registered for all messages with no chat/user whitelist; anyone who discovers the bot's username can message it. Impact today is minor (no data exposed), but there is no allowlist for when handlers are added.
- **Recommended fix:** Restrict handlers to the configured `telegram_chat_id` (or a user-id allowlist) via a dispatcher filter/middleware.

#### SEC-14 — Attacker-influenced LLM output written to logs with embedded newlines (log injection)
- **Severity:** LOW
- **Status:** OPEN
- **Category:** Logging
- **File:line:** `src/analyzer/risk.py:87, 91, 96`
- **Description:** On malformed LLM replies, the raw model output (up to 200 chars) is logged verbatim. The reply is shaped by PR title/body/diff, which the PR author controls; embedded newlines can forge log lines (e.g., something that looks like `INFO ... notified about PR 999 (risk=LOW)`), complicating incident response.
- **Recommended fix:** Log a newline-stripped/escaped representation (or a hash + length) instead of the raw text.

#### SEC-15 — `pull_request.body` not type-validated, unlike the other PR fields
- **Severity:** LOW
- **Status:** OPEN
- **Category:** Input validation
- **File:line:** `src/webhook/server.py:294` (`body=pr.get("body") or ""`), `src/analyzer/risk.py:64-65`
- **Description:** `number`, `title`, and `html_url` are strictly type-checked (including a `bool`-is-not-`int` guard, `src/webhook/server.py:280-288`), but `body` is passed through with `or ""`. A non-string JSON value (number, list, object) is accepted and stringified into the LLM prompt. No code-injection impact (prompt-only), but it is an inconsistency in the validation boundary.
- **Recommended fix:** Validate `isinstance(pr.get("body"), str)` (default `""`) alongside the other field checks; optionally cap body length before prompt assembly.

#### SEC-16 — Test-repo unit tests are tautological (`assert True`)
- **Severity:** LOW
- **Status:** OPEN
- **Category:** Test quality
- **File:line:** `test-repo/tests/test_main.py:4`, `test-repo/tests/test_api.py:4`, `test-repo/tests/test_models.py:4`
- **Description:** All three test-repo unit tests are `assert True`; they pass even if the module under test is deleted. False confidence. (These are fixture files regenerated by `create_test_repo.sh`.)
- **Recommended fix:** Replace with real assertions against the functions under test.

#### SEC-17 — Root `requirements.txt` is incomplete/inconsistent with `pyproject.toml`
- **Severity:** LOW
- **Status:** OPEN
- **Category:** Dependencies
- **File:line:** `requirements.txt:1-2`
- **Description:** `requirements.txt` lists only `openai>=1.0` and `aiosqlite>=0.19`, omitting `aiogram`, `fastapi`, `uvicorn`, `httpx`, `PyGithub`, `pydantic`, `pydantic-settings` declared in `pyproject.toml`. `pip install -r requirements.txt` yields a non-runnable app.
- **Recommended fix:** Single source of truth in `pyproject.toml`; generate `requirements.txt` from it (or delete it).

#### SEC-18 — Temp DB unlink swallows `OSError` (`scripts/test_graph_only.py`)
- **Severity:** LOW
- **Status:** OPEN
- **Category:** Test tooling
- **File:line:** `scripts/test_graph_only.py:58-61`
- **Description:** The temporary SQLite DB is created with `NamedTemporaryFile(delete=False)` and removed in `finally` with `except OSError: pass`. A failed unlink (e.g., file locked) leaves a stray temp file with no signal.
- **Recommended fix:** Log the unlink failure, or use `tempfile.TemporaryDirectory`/`mkstemp` context management.

#### SEC-19 — Broad `except Exception` clauses in test-repo code hide failures
- **Severity:** LOW
- **Status:** OPEN
- **Category:** Test fixture code
- **File:line:** `test-repo/src/main.py:23-25` (decorator collapses all errors to `None`), `test-repo/src/database.py:46` (retry swallows, re-raises on final attempt)
- **Description:** Broad exception handlers in the generated fixture code. Low impact (fixture only), but the `None`-returning decorator makes errors indistinguishable from normal results.
- **Recommended fix:** Catch specific exception types; re-raise or return a distinct sentinel in the decorator.

#### SEC-26 — Replay dedup is check-then-mark, not atomic *(new in v2)*
- **Severity:** LOW
- **Status:** OPEN
- **Category:** Webhook security / concurrency
- **File:line:** `src/webhook/server.py:257-261` (`is_delivery_seen` → `mark_delivery_seen`), `src/db/database.py:86-110`
- **Description:** The replay check and the mark are two separate awaited statements with no transaction spanning them. Two identical deliveries (same `X-GitHub-Delivery`) arriving concurrently can both read "not seen" before either writes "seen", so both run the full paid pipeline: duplicate LLM call + duplicate Telegram message. `INSERT OR IGNORE` deduplicates the *row* but not the *processing*.
- **Recommended fix:** Make the claim atomic: `INSERT ... ON CONFLICT (delivery_id) DO NOTHING` and treat `rowcount == 1` as "claimed" (skip on `0`); drop the separate `is_delivery_seen` pre-check.

#### SEC-27 — Chunked / missing `Content-Length` bypasses the 5 MiB body cap *(new in v2)*
- **Severity:** LOW
- **Status:** OPEN (residual of fixed SEC-03)
- **Category:** DoS / resource
- **File:line:** `src/webhook/server.py:233-242`
- **Description:** The 413 cap is enforced only when a parsable `Content-Length` header is present. A request using chunked transfer encoding (no `Content-Length`) skips the check entirely, and `await request.body()` then buffers an unbounded body. GitHub always sends `Content-Length`, so exposure is limited to non-GitHub senders reaching the port.
- **Recommended fix:** Reject (411/413) requests without a parsable `Content-Length`, or stream-read with a byte budget and abort past the limit.

#### SEC-28 — `create_test_repo.sh` regenerates `test-repo/requirements.txt` with old CVE-affected pins *(new in v2)*
- **Severity:** LOW
- **Status:** OPEN (regression risk for fixed SEC-07)
- **Category:** Dependencies / dev tooling
- **File:line:** `create_test_repo.sh:123-127` (`flask==2.3.0`, `sqlalchemy==2.0.0`, `pytest==7.4.0`), `:213` (`requests==2.31.0`), `:515` (`jinja2==3.1.2`)
- **Description:** `test-repo/requirements.txt` was fixed (SEC-07: `flask>=3.0`, `sqlalchemy>=2.0.30`, `pytest>=8.0`, `requests>=2.32`, `jinja2>=3.1.6`), but the script that (re)creates the test repo still emits the old exact pins, which carry CVE-2023-30168/CVE-2023-32309 (flask 2.3.0), CVE-2024-4708 (requests 2.31.0), CVE-2024-34057/CVE-2024-56704 (jinja2 3.1.2). Re-running the script silently reverts the fix.
- **Recommended fix:** Update the script's `requirements.txt` heredoc and the two `echo` appends to the same `>=` specifiers as the current `test-repo/requirements.txt`.

### INFO

#### SEC-20 — OpenAI `base_url` is operator-supplied; a non-TLS base URL sends the API key in cleartext
- **Severity:** INFO
- **Status:** OPEN
- **Category:** External API calls
- **File:line:** `src/config.py:11`, `src/analyzer/risk.py:146`, `.env.example:4`
- **Description:** `openai.AsyncOpenAI(..., base_url=base_url)` uses the configured URL verbatim; nothing validates the scheme. `OPENAI_BASE_URL=http://...` (e.g., a local proxy) sends `OPENAI_API_KEY` unencrypted.
- **Recommended fix:** Validate that a set `openai_base_url` starts with `https://`.

#### SEC-21 — asyncio task failures are only observed at shutdown
- **Severity:** INFO
- **Status:** OPEN
- **Category:** Availability / asyncio
- **File:line:** `src/main.py:100-101` (tasks created), `:108-111` (results inspected only after `stop_event`)
- **Description:** If `bot.start()` raises early (e.g., invalid Telegram token), the bot is silently down for the process lifetime; the error is logged only at shutdown.
- **Recommended fix:** Add a done-callback that logs immediately (and optionally exits) when a task fails before shutdown.

#### SEC-22 — Unauthenticated requests get a 400 event-type oracle before the 401; `action` reflected in error detail
- **Severity:** INFO
- **Status:** OPEN
- **Category:** Webhook security
- **File:line:** `src/webhook/server.py:244-247` (event check before signature), `:270-272` (`action` reflected in 400 detail)
- **Description:** The `X-GitHub-Event` header is checked before the signature, so an unauthenticated client can distinguish "wrong event" (400) from "bad signature" (401), and the attacker-controlled `action` value is reflected in the 400 detail. FastAPI JSON-serializes it (not XSS); minor information disclosure.
- **Recommended fix:** Verify the signature first and return one generic 401 for all unauthenticated rejections.

#### SEC-23 — Dependency manifest inconsistency (context)
- **Severity:** INFO
- **Status:** OPEN
- **Category:** Dependencies
- **File:line:** `requirements.txt:1-2`, `pyproject.toml:10-20`
- **Description:** `requirements.txt` (2 deps) and `pyproject.toml` (9 runtime deps) diverge; `PyGithub` (`pyproject.toml:16`) is never imported anywhere in `src/` (verified by grep). Install paths can diverge.
- **Recommended fix:** Drop `PyGithub` if unused; generate `requirements.txt` from `pyproject.toml`.

#### SEC-24 — No real (non-placeholder) hardcoded secrets in tests/scripts/config
- **Severity:** INFO (positive)
- **Status:** OPEN (re-verified)
- **Category:** Secrets management
- **File:line:** `.env.example:1-10`; `tests/test_webhook.py:31-32, 82-85`; `scripts/test_e2e.py:70-73` (prints `<set>`/`<missing>` only, never values)
- **Description:** Re-verified: all token/key-looking values are placeholders (`"test-github-token"`, `"test-webhook-secret"`, `"123:TEST"`, `"test-openai-key"`, `your-...`). No `sk-…`, `ghp_…`, `github_pat_…`, `xoxb-…` patterns anywhere in scope. `.env` is in `.gitignore`.
- **Recommended fix:** None; keep the convention.

---

## Fixed findings (verified in current code)

| ID | Fix | Verification in current code |
|----|-----|------------------------------|
| SEC-01 | `create_test_repo.sh` no longer hard-codes a foreign absolute path | `create_test_repo.sh:4-8` — `REPO_DIR` derived from script location with optional `$1` override |
| SEC-02 | Dedicated webhook secret | `src/config.py:9` (required `github_webhook_secret`), `src/webhook/server.py:248-252` (HMAC key); `github_token` used only for the Bearer header (`:145`) |
| SEC-03 | Body size cap before read | `src/webhook/server.py:41, 233-240` — 413 for `Content-Length` > 5 MiB before `request.body()`. Residual: SEC-27 |
| SEC-04 | 202 + bounded background queue | `src/webhook/server.py:42, 209, 300-304` — handler returns 202, `asyncio.Queue(maxsize=100)`, single worker (`:127-136`), 503 when full |
| SEC-05 | Strict shell mode + cleanup trap | `create_test_repo.sh:2, 10-13` — `set -euo pipefail`, `trap cleanup ERR INT TERM` |
| SEC-07 | Test-repo pins past cited CVEs | `test-repo/requirements.txt:1-5` — `flask>=3.0`, `sqlalchemy>=2.0.30`, `pytest>=8.0`, `requests>=2.32`, `jinja2>=3.1.6`. Residual: SEC-28 |
| SEC-08 | Upper bounds on all deps | `pyproject.toml:10-30` — every runtime and dev dep bounded (e.g., `aiogram>=3.4,<4`) |
| SEC-09 | Replay protection via `X-GitHub-Delivery` | `src/webhook/server.py:255-261` + `src/db/database.py:96-110` — seen-deliveries table with 24 h retention. Residual: SEC-26. (v1 never marked this fixed despite the code being present.) |
| SEC-29 | `.env.example` restored `OPENAI_API_KEY` | Commit 6250af1 (2026-09-29) overwrote the `OPENAI_API_KEY=...` line when adding `GITHUB_WEBHOOK_SECRET` (an edit slip, not intentional); a `.env` copied from the example failed validation because `openai_api_key` is required with no default (`src/config.py:10`). Line restored 2026-09-30. |

---

## Correct controls (re-verified, current line numbers)

Do not re-report:

1. **Timing-safe HMAC-SHA256 over the raw body, dedicated secret** — `src/webhook/server.py:72-77`: `hmac.new(secret, body, sha256)` compared with `hmac.compare_digest`; rejects missing header and non-`sha256=` prefixes.
2. **Signature checked before payload parsing/use** — `src/webhook/server.py:248-253` precedes `json.loads` (`:263-266`); unauthenticated requests never reach DB, LLM, or Telegram.
3. **Event/action filtering** — `X-GitHub-Event == "pull_request"` (`:244`) and `action ∈ {"opened", "synchronize"}` (`:43, 270-272`).
4. **Strict webhook field validation** — `number` non-`bool` int, `title`/`html_url` strings (`:277-288`); `pr_number` validated before interpolation into the GitHub API path (`:103-105`).
5. **GitHub file-list entries validated before use** — only `dict` entries with a `str` `filename` are kept (`:91-97`).
6. **All SQL parameterized** — `src/db/database.py:58-62, 68-72, 89-92`; `src/graph/expertise.py:176-188, 249-254, 297-301` (the `IN (...)` f-string is built from `?` placeholders only).
7. **No shell in subprocess; path from operator env, not webhook** — `asyncio.create_subprocess_exec("git", "log", ...)`, `cwd` from `REPO_PATH` env (`src/graph/expertise.py:142-150`, `src/main.py:66-68`).
8. **Git SHAs validated as 40 hex chars before use as row keys** — `src/graph/expertise.py:26, 216-220`.
9. **Timeouts on all outbound HTTP; TLS verified by default** — GitHub client 30 s (`src/webhook/server.py:40, 141-149`); OpenAI 30 s client timeout + `asyncio.wait_for` guard (`src/analyzer/risk.py:28, 146, 162-165`); no `verify=False`/`ssl=False` anywhere in `src/`.
10. **Bounded retry with exponential backoff** — 429s and timeouts: 2 s base / 30 s cap / max 3 retries, then graceful `HIGH` degradation (`src/analyzer/risk.py:160-202`).
11. **Diff truncated before LLM** — `MAX_DIFF_CHARS = 50_000` (`src/analyzer/risk.py:32, 60-62`); PR file paths used only in SQL, never on the filesystem.
12. **Strict, non-raising LLM output parsing** — malformed JSON / non-object / unknown level → `HIGH`, confidence 0; level validated against a fixed set; confidence clamped to [0,1]; reasons capped at 2 (`src/analyzer/risk.py:70-111`).
13. **Complete MarkdownV2 escaping of all dynamic Telegram content** — title, reasons, reviewer logins, URL escaped with the full special set, backslash handled first (`src/notifications/composer.py:11-24, 108, 112, 114, 118`); sent with `parse_mode="MarkdownV2"` (`src/bot/bot.py:54-56`); chat id is a settings `int`.
14. **Secrets via required settings, no hardcoded values** — `telegram_bot_token`, `github_token`, `github_webhook_secret`, `openai_api_key` have no defaults (`src/config.py:7-10, 14`); no token/key literals in `src/`.
15. **Secrets not logged** — logs use lazy %-formatting with PR numbers, risk levels, exception classes, `<set>`/`<missing>` flags only; HTTP error responses are generic.
16. **No unsafe deserialization** — no `pickle`, `eval`, `exec`, or `shell=True` in `src/`.
17. **SSRF-safe GitHub calls** — fixed `https://api.github.com` constant; no user-controlled URLs fetched (`src/webhook/server.py:38, 84-90`).
18. **Idempotent resource cleanup** — GitHub client, graph DB, history DB, bot session closed on shutdown (`src/webhook/server.py:171-176`, `src/main.py:113-115`); task results collected with `return_exceptions=True`.
19. **Bounded queue backpressure** — 503 when the 100-item queue is full (`src/webhook/server.py:300-304`); single sequential worker bounds concurrency to 1.
20. **24 h replay dedup** — `seen_deliveries` table with cutoff purge on every mark (`src/db/database.py:96-110`).

---

## Dependency table (current state)

| Package | Version specifier | Source file | Note |
|---------|-------------------|-------------|------|
| aiogram | `>=3.4,<4` | `pyproject.toml:11` | Bounded |
| fastapi | `>=0.100,<1` | `pyproject.toml:12` | Bounded |
| uvicorn | `>=0.23,<1` | `pyproject.toml:13` | Bounded |
| httpx | `>=0.24,<1` | `pyproject.toml:14` | Bounded |
| openai | `>=1.0,<2` | `pyproject.toml:15` | Bounded |
| PyGithub | `>=2.0,<3` | `pyproject.toml:16` | Bounded; **unused in `src/`** (SEC-23) |
| aiosqlite | `>=0.19,<1` | `pyproject.toml:17` | Bounded |
| pydantic | `>=2.0,<3` | `pyproject.toml:18` | Bounded |
| pydantic-settings | `>=2.0,<3` | `pyproject.toml:19` | Bounded |
| starlette | *(not listed)* | — | Indirect via FastAPI; not pinned anywhere |
| pytest | `>=8,<9` | `pyproject.toml:24` | Dev, bounded |
| pytest-asyncio | `>=0.23,<1` | `pyproject.toml:25` | Dev, bounded |
| pytest-cov | `>=5,<8` | `pyproject.toml:26` | Dev, bounded |
| ruff | `>=0.4,<1` | `pyproject.toml:27` | Dev, bounded |
| mypy | `>=1.8,<2` | `pyproject.toml:28` | Dev, bounded |
| black | `>=24,<26` | `pyproject.toml:29` | Dev, bounded |
| openai | `>=1.0` | `requirements.txt:1` | Incomplete manifest (SEC-17/23) |
| aiosqlite | `>=0.19` | `requirements.txt:2` | Incomplete manifest (SEC-17/23) |
| flask | `>=3.0` | `test-repo/requirements.txt:1` | CVEs resolved (SEC-07); **script reverts it** (SEC-28) |
| sqlalchemy | `>=2.0.30` | `test-repo/requirements.txt:2` | Current (SEC-07/28) |
| pytest | `>=8.0` | `test-repo/requirements.txt:3` | Current (SEC-07/28) |
| requests | `>=2.32` | `test-repo/requirements.txt:4` | CVE-2024-4708 resolved (SEC-07/28) |
| jinja2 | `>=3.1.6` | `test-repo/requirements.txt:5` | CVE-2024-34057/56704 resolved (SEC-07/28) |

**Pinning status:** All main-project runtime and dev deps are bounded (SEC-08 fixed). No lockfile exists; `starlette` floats indirectly. `requirements.txt` is an incomplete second source of truth (SEC-17/23).

---

## Suggested remediation order

1. **SEC-25** — one line: sign `test_e2e.py` with `github_webhook_secret` (restores the only E2E exercise of the signature path).
2. **SEC-06** — make the e2e script opt-in + hermetic (dummy creds, mocked OpenAI/Telegram, ephemeral port).
3. **SEC-26** — atomic delivery claim (`ON CONFLICT DO NOTHING` + `rowcount`).
4. **SEC-28** — sync `create_test_repo.sh` pins with `test-repo/requirements.txt`.
5. **SEC-27** — reject or budget chunked bodies without `Content-Length`.
6. Remaining LOW/INFO items per their recommended fixes.
