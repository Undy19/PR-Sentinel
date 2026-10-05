# PR Sentinel
[English](README.md) | **Русский**

[![CI](https://github.com/Undy19/pr-sentinel/actions/workflows/ci.yaml/badge.svg)](https://github.com/Undy19/pr-sentinel/actions/workflows/ci.yaml)
[![Version](https://img.shields.io/badge/version-0.1.0)](CHANGELOG.md)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Telegram-бот (aiogram 3.x) и FastAPI-webhook, автоматизирующие первичный отбор PR
для GitHub-репозитория. При каждом событии `pull_request` бот генерирует краткую
3-строчную оценку риска (LOW / MED / HIGH / CRITICAL) через OpenAI API за ~60
секунд, затем рекомендует 1–2 релевантных ревьюеров на основе графа эксперти
из git-истории, хранящегося в SQLite.

> **🤖 AI-assisted development (vibe-coded).** Концепция проекта была создана
> руководителем проекта; реализация выполнена с помощью ИИ (LLM-агенты для
> написания кода). Финальное состояние было проверено и утверждено людьми.

## Технологический стек

Python 3.11+, aiogram 3.x, FastAPI, httpx, OpenAI API, SQLite.

## Возможности

- **Оценка риска** — LLM-оценка риска PR в 3 строках (LOW / MED / HIGH / CRITICAL) с конкретными причинами и уровнем уверенности, генерируется за ~60 с
- **Обработка rate-limit** — повторные запросы при OpenAI 429 с экспоненциальным backoff (база 2 с, лимит 30 с, 3 попытки)
- **Рекомендация ревьюеров** — граф эксперти из git-истории (SQLite) ранжирует коммитеров по частоте и свежести изменений; рекомендует 1–2 ревьюеров на PR
- **Безопасность webhook** — HMAC-проверка `X-Hub-Signature-256`, защита от повторных запросов по `X-GitHub-Delivery`, лимит тела 5 МБ
- **Асинхронный конвейер** — фоновая очередь обработки (100 элементов); webhook подтверждает сразу, анализ выполняется асинхронно
- **Локализация** — уведомления в Telegram на русском (по умолчанию) или английском (`NOTIFICATION_LANGUAGE=en`)
- **CI/CD** — ruff (lint + форматирование), mypy (strict), pytest (порог покрытия 60 %), commitlint (Conventional Commits)

## Быстрый старт

```
pip install -e ".[dev]"
cp .env.example .env
```

| Переменная | Обязательна | Описание |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | да | Токен Telegram Bot API |
| `GITHUB_TOKEN` | да | GitHub PAT или fine-grained токен |
| `GITHUB_WEBHOOK_SECRET` | да | HMAC-секрет для `X-Hub-Signature-256` (настраивается в конфигурации webhook GitHub) |
| `OPENAI_API_KEY` | да | Ключ OpenAI API (или `none` для локального vLLM) |
| `OPENAI_BASE_URL` | нет | По умолчанию `https://api.openai.com/v1` |
| `OPENAI_MODEL` | нет | По умолчанию `gpt-4o` |
| `GITHUB_REPO` | да | `owner/repo` для мониторинга |
| `TELEGRAM_CHAT_ID` | да | ID целевого чата/канала |
| `DATABASE_PATH` | нет | По умолчанию `pr_sentinel.db` |
| `NOTIFICATION_LANGUAGE` | нет | `ru` (по умолчанию) или `en` |

Полный справочник: [`docs/architecture.md`](docs/architecture.md) § Configuration.

## Запуск приложения

После настройки переменных окружения (см. таблицу выше) запустите сервисы:

**Бот + webhook в одном процессе:**

```bash
python -m pr_sentinel.main
```

**Только сервер webhook:**

```bash
uvicorn pr_sentinel.webhook.server:app
```

**Запуск тестов:**

```bash
pytest
```

**Запуск тестов с порогом покрытия (минимум 60%):**

```bash
pytest --cov=pr_sentinel --cov-fail-under=60
```

Полный рабочий процесс разработки (lint, форматирование, проверка типов, коммиты и PR) описан в [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Пример уведомления

Что бот отправляет в чат для каждого нового PR (подписи шаблона по умолчанию на русском; `NOTIFICATION_LANGUAGE=en` переключает их на английский):

```
🔀 PR: Add retry on 429 rate-limit
📊 Риск: 🟡 Средний
💡 Изменения затрагивают аутентификацию • Добавлен код без тестов
👥 Ревьюеры: @alice, @bob
🔗 https://github.com/owner/repo/pull/42
```

## Документация

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — настройка, стиль, тестирование, коммиты, PR, защита веток
- [`COMMIT_CONVENTIONS.md`](COMMIT_CONVENTIONS.md) — справочник по Conventional Commits
- [`SECURITY.md`](SECURITY.md) — политика безопасности, сообщение об уязвимостях, поддерживаемые версии
- [`docs/architecture.md`](docs/architecture.md) — архитектура, HTTP API, модель данных, конфигурация

## Лицензия

Проект распространяется под лицензией MIT. Подробности — в файле [LICENSE](LICENSE).
