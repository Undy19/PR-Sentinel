# Security Audit — `src/` (PR Sentinel)

- **Scope audited:** `src/` recursively (15 files, all read in full), plus `pyproject.toml`, `requirements.txt`, `.env.example` for dependency/secret context.
- **Date:** 2026-09-29
- **Method:** static review; every finding below cites code that was actually read. No files were modified.

## Summary

**Overall risk: MODERATE.** The core security posture is sound: the GitHub webhook is protected by a timing-safe HMAC-SHA256 check over the raw body, all SQL is parameterized, the git subprocess uses an argument list (no shell), all outbound HTTP has timeouts with TLS verification, LLM output is strictly parsed and degraded, and all dynamic Telegram content is MarkdownV2-escaped. No CRITICAL or HIGH findings.

The main weaknesses are **availability/cost-oriented**: the webhook endpoint has no rate limiting, no body-size cap, and does the full GitHub + LLM + Telegram pipeline synchronously inside the request (GitHub will time out and redeliver, amplifying load and OpenAI spend). There is also **no dedicated webhook secret** (the GitHub API token doubles as the HMAC secret) and **no replay protection**.

| Severity | Count |
|----------|-------|
| CRITICAL | 0 |
| HIGH     | 0 |
| MEDIUM   | 3 |
| LOW      | 7 |
| INFO     | 4 |

Context note (not a src/ finding): `requirements.txt` lists only `openai` and `aiosqlite`, while `pyproject.toml` declares nine dependencies; `PyGithub` is declared in `pyproject.toml:16` but never imported anywhere in `src/` (the GitHub API is called via `httpx` directly).

---

## Findings

### MEDIUM

#### SRC-01 — Webhook HMAC secret is the GitHub API token (no dedicated secret)
- **Severity:** MEDIUM
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

#### SRC-02 — Unbounded request body read before authentication
- **Severity:** MEDIUM
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

#### SRC-03 — No rate limiting, concurrency bound, or queue on the webhook; long inline processing
- **Severity:** MEDIUM
- **Category:** DoS / resource / Webhook security
- **File:line:** `src/webhook/server.py:162-232` (handler), `src/webhook/server.py:205-221` (GitHub fetch + LLM), `src/analyzer/risk.py:160-202` (retry loop)
- **Description:** Each `/webhook/github` request performs, inline and sequentially: 2 GitHub API calls (30 s timeout each) + an OpenAI call (up to 4 attempts × 30 s timeout + 2/4/8 s backoff ≈ 150 s worst case) + a Telegram send, and only then returns 200. There is no rate limiting, no concurrency limit, and no background queue. Consequences: (a) a sender holding a valid signature (see SRC-01) can open many concurrent deliveries, each consuming OpenAI quota (direct cost) and GitHub API rate limit; (b) because the response routinely exceeds GitHub's delivery timeout, GitHub marks deliveries failed and redelivers them, multiplying LLM calls and Telegram duplicates for the same PR.
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
- **Recommended fix:** Return 202 quickly and process the delivery in a bounded worker queue (e.g., `asyncio.Semaphore` / queue with a max size); add per-endpoint rate limiting (e.g., by `X-GitHub-Delivery` or source IP); deduplicate by delivery id (see SRC-04).

### LOW

#### SRC-04 — No replay protection (no `X-GitHub-Delivery` deduplication)
- **Severity:** LOW
- **Category:** Webhook security
- **File:line:** `src/webhook/server.py:162-232` (handler ignores `X-GitHub-Delivery`)
- **Description:** Deliveries are verified by signature only. A captured (or GitHub-redelivered) delivery can be replayed at any time, re-triggering the full pipeline: duplicate Telegram notification, extra LLM cost, and a new `pr_history` row.
- **Evidence:** no reference to `X-GitHub-Delivery` anywhere in `src/webhook/server.py`.
- **Recommended fix:** Keep a short-lived (e.g., 24 h) seen-set of `X-GitHub-Delivery` ids (in SQLite) and skip duplicates.

#### SRC-05 — Unbounded LLM reason length can exceed Telegram's 4096-char limit; failure surfaces as 500 + redelivery
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

#### SRC-06 — `pr_history` grows unbounded; no uniqueness on `pr_number`
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

#### SRC-07 — Swagger UI and OpenAPI schema exposed on the public webhook server
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

#### SRC-08 — Telegram bot has no access control
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

#### SRC-09 — Attacker-influenced LLM output written to logs with embedded newlines (log injection)
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

#### SRC-10 — `pull_request.body` not type-validated, unlike the other PR fields
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

### INFO

#### SRC-11 — OpenAI `base_url` is operator-supplied; a non-TLS base URL would send the API key in cleartext
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

#### SRC-12 — asyncio task failures are only observed at shutdown
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

#### SRC-13 — Unauthenticated requests get a 400 event-type oracle before the 401; `action` reflected in error detail
- **Severity:** INFO
- **Category:** Webhook security
- **File:line:** `src/webhook/server.py:169-172`, `src/webhook/server.py:185-187`
- **Description:** The `X-GitHub-Event` header is checked before the signature, so an unauthenticated client can distinguish "wrong event" (400) from "bad signature" (401), and the raw (attacker-controlled) `action` value is reflected in the 400 detail (`f"action {action!r} is not processed"`). The response is JSON-serialized by FastAPI, so this is not XSS, and the value is bounded only by the (unbounded, see SRC-02) body. Minor information disclosure.
- **Evidence:**
  ```python
  # src/webhook/server.py:169-172
  if request.headers.get("X-GitHub-Event") != "pull_request":
      raise HTTPException(status_code=400, detail="expected X-GitHub-Event: pull_request")
  ```
- **Recommended fix:** Verify the signature first and return a single generic 401/400 for all unauthenticated rejections.

#### SRC-14 — Dependency manifest inconsistency (context)
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
