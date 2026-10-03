# Commit Conventions

This repository uses [Conventional Commits 1.0](https://www.conventionalcommits.org/spec/).
Commit messages are linted by [commitlint](https://commitlint.js.org/) (see
`commitlint.config.js`) in the `Commitlint` CI workflow, so non-conforming
commits fail on `pull_request` and on push to `main`.

## Format

```
<type>(<optional scope>): <short summary>

[optional body]

[optional footer(s)]
```

- `type` is required; `scope` (e.g. `analyzer`, `bot`, `webhook`, `db`) is
  optional; the summary should be imperative and at most 72 characters.
- A breaking change is marked with `!` after the type/scope and/or a
  `BREAKING CHANGE:` footer describing the change.

## Allowed types

| Type       | Use for                                             |
| ---------- | --------------------------------------------------- |
| `feat`     | A new feature                                       |
| `fix`      | A bug fix                                           |
| `docs`     | Documentation only                                  |
| `style`    | Formatting; no code change                          |
| `refactor` | Change that neither fixes a bug nor adds a feature  |
| `perf`     | A performance improvement                           |
| `test`     | Adding or correcting tests                          |
| `build`    | Build system or dependencies                        |
| `ci`       | CI configuration (GitHub Actions, etc.)             |
| `chore`    | Maintenance that fits nowhere else                  |
| `revert`   | Reverting a previous commit                         |

## Examples

```
feat(analyzer): add retry on 429
fix(bot): ignore message updates from the bot itself
ci: add commitlint job for pull requests
docs(webhook): document the PR payload schema
feat(api)!: rename /check endpoint to /analyze

BREAKING CHANGE: clients must call /analyze instead of /check
```

## References

- Conventional Commits spec 1.0: https://www.conventionalcommits.org/spec/
- commitlint: https://commitlint.js.org/
