# Security Policy

## Supported Versions

Security fixes are provided for the latest 0.1.x release. Older releases are
not actively maintained.

## Reporting a Vulnerability

Please **do not** report security vulnerabilities through public GitHub issues.

The preferred channel is a **private GitHub Security Advisory**:

- Open a new advisory at [`github.com/Undy19/test/security/advisories/new`](https://github.com/Undy19/test/security/advisories/new)
  (repo → **Security** tab → **New advisory**).
- If GitHub's security feature is not available for your account, email the
  maintainer and mark the subject `[SECURITY]`.

A good report includes:

1. A short description of the vulnerability and its impact.
2. The affected version/commit.
3. A minimal reproduction (steps, payload, or test case) where possible.
4. A suggested severity (Critical / High / Medium / Low) if you have one.

## Response Timeline

| Step | Target |
|---|---|
| Acknowledgement | 3 business days |
| Triage and severity assessment | 7 calendar days |
| Fix or documented mitigation | 30 calendar days |

These are target timelines, not guaranteed SLAs.

Once fixed, we publish a [GitHub Security Advisory](https://github.com/Undy19/test/security/advisories)
describing the issue, the affected versions, and the fix, and we credit the
reporter (anonymously if requested).

## Security-Relevant Design

The following controls are built into the application; they are the first
places to look when triaging a report:

- **Webhook authentication** — every delivery to `POST /webhook/github` must
  carry a valid `X-Hub-Signature-256` HMAC-SHA256 signature
  (`src/pr_sentinel/webhook/server.py`, constant-time comparison via
  `hmac.compare_digest`). The HMAC key is the required `GITHUB_WEBHOOK_SECRET`
  — a dedicated random value, distinct from `GITHUB_TOKEN` (SEC-02).
- **Replay protection** — enabled by default and should remain enabled in
  production. `X-GitHub-Delivery` ids are deduplicated in the `seen_deliveries`
  table (24 h retention); duplicates are answered `200 {"status":
  "duplicate"}` without re-processing. Set `REPLAY_PROTECTION_ENABLED=false`
  only temporarily (e.g., re-running a captured payload in local debugging).
- **Input size limits** — request bodies above 5 MB are rejected with `413`;
  the background work queue caps at 100 items and returns `503` when full.
- **Secrets handling** — all secrets (`TELEGRAM_BOT_TOKEN`, `GITHUB_TOKEN`,
  `GITHUB_WEBHOOK_SECRET`, `OPENAI_API_KEY`) are read from environment
  variables at startup (`.env`, which is gitignored). They are never logged
  and never appear in notifications.
- **Data access** — all SQLite access uses parameterized queries
  (`?` placeholders, `src/pr_sentinel/db/database.py`); no string-interpolated
  SQL.
- **Notification output** — every dynamic fragment in Telegram messages
  (PR title, risk reasons, reviewer logins, URL) is escaped for MarkdownV2
  (`src/pr_sentinel/notifications/composer.py`), so PR-provided text cannot
  inject formatting or markup.
- **LLM output treated as untrusted** — the OpenAI response is parsed
  strictly; any parse failure falls back to a conservative `HIGH` risk
  result, and the call is bounded by a 45 s timeout with 3 backoff retries on
  `429`.
- **External calls** — GitHub REST requests use a 30 s timeout
  (`src/pr_sentinel/webhook/server.py`).

## Secret Rotation

If a token is exposed (accidental commit, log leak, shared screen):

1. Rotate it: reissue the bot token in BotFather (Telegram), create a new PAT
   (GitHub), or create a new API key (OpenAI).
2. If `GITHUB_WEBHOOK_SECRET` was exposed, generate a new random value and
   update it in the GitHub webhook configuration (Settings → Webhooks →
   **Secret**).
3. Update `.env` and restart the service.
4. Remove the old value from anywhere it appeared (git history, CI logs).

## Out of Scope

Vulnerabilities in the underlying platforms — the Telegram Bot API, the
GitHub API, the OpenAI API, CPython, and third-party packages (aiogram,
FastAPI, httpx, aiosqlite, pydantic) — should be reported to those
maintainers; we will follow upstream advisories and bump dependencies as
needed.
