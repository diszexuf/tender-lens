# Tender Lens

Система обоснования начальной (максимальной) цены договора (НМЦК) методом сопоставимых рыночных цен: по описанию закупки ищет сопоставимые предложения в открытых источниках, оценивает их сопоставимость, исключает аномалии и формирует проверяемый отчёт.

> Статус: **этап 1** — сквозной расчёт НМЦК в UI и API ([план](docs/04-implementation-plan.md)).

## Документация

- [Требования](docs/01-requirements.md)
- [Пользовательские сценарии](docs/02-use-cases.md)
- [Архитектура](docs/03-architecture.md)
- [План реализации](docs/04-implementation-plan.md)

## Требования к окружению

| Компонент | Версия / объём |
|---|---|
| Docker Engine + Compose | Docker 27+, Compose v2.24+ |
| GPU (рекомендуется) | NVIDIA с 6 ГБ VRAM и драйвером с поддержкой CUDA; на Windows — Docker Desktop с WSL2 |
| RAM / диск | 16 ГБ / 20 ГБ свободного места (образы ~3 ГБ, модель ~2,5 ГБ) |
| Python | 3.14 — только для генерации `.env` и локальной разработки |

## Быстрый старт

```bash
# 1. Создать .env со случайными секретами (или скопировать .env.example и заполнить вручную)
python infra/init/gen_env.py

# 2. Собрать и запустить всё
docker compose up -d --build

# 3. Открыть
#    UI:       http://localhost:8080   (раздел «Состояние» — готовность сервисов)
#    Swagger:  http://localhost:8000/api/docs
```

Первый запуск скачивает образы и модель LLM (`OLLAMA_MODEL`), это занимает несколько минут. Пока модель загружается, компонент `llm` на странице состояния имеет статус «Частично».

**Без GPU:**

```bash
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d --build
```

**Остановка:**

```bash
docker compose down        # остановить, данные сохраняются в томах
docker compose down -v     # остановить и удалить все данные (БД, хранилище, модели)
```

## Сервисы

| Сервис | Назначение | Порт на хосте |
|---|---|---|
| `web` | Vue 3 + Vuetify SPA, nginx (проксирует `/api`) | `WEB_PORT` (8080) |
| `api` | FastAPI | `API_PORT` (8000) |
| `worker` | Taskiq-воркер пайплайна (с Chromium) | — |
| `postgres` | PostgreSQL 18 | — |
| `redis` | Valkey: очередь задач, кэш | — |
| `garage` | S3-хранилище снапшотов и отчётов | — |
| `ollama` | Локальная LLM | — |
| `searxng` | Метапоиск | — |
| `init` | Одноразово: миграции БД, настройка Garage (идемпотентно) | — |
| `ollama-pull` | Одноразово: загрузка модели LLM | — |

Внешние сервисы: только открытые источники данных (ЕИС, ГИСП, сайты поставщиков) и поисковые системы через SearXNG. Внешние LLM API не используются.

## Переменные окружения

Полный список с комментариями — в [.env.example](.env.example). Основные:

| Переменная | По умолчанию | Описание |
|---|---|---|
| `WEB_PORT`, `API_PORT` | 8080, 8000 | Порты на хосте |
| `POSTGRES_*` | — | Подключение к БД |
| `GARAGE_*`, `S3_*` | — | Секреты Garage и ключ S3 (генерируются `gen_env.py`) |
| `OLLAMA_MODEL` | `qwen3:4b` | Модель LLM; `qwen2.5:7b` точнее, но на 6 ГБ VRAM частично уходит на CPU |
| `OLLAMA_NUM_CTX` | 8192 | Контекст модели |
| `SEARXNG_SECRET` | — | Секрет SearXNG |
| `EIS_TOKEN` | пусто | Токен сервиса данных ЕИС (необязательно) |

## Проверка системы

Системные тесты проверяют развёрнутую систему как чёрный ящик — только по HTTP через nginx, без доступа к коду: готовность всех сервисов, соответствие API контракту `contracts/openapi.json`, работу SPA и прокси.

```bash
# В контейнере, внутри сети Compose (стек должен быть запущен)
docker compose --profile test run --rm --build system-tests

# Или локально против http://localhost:8080
cd tests/system
python -m venv .venv && .venv/Scripts/pip install uv && .venv/Scripts/uv sync
.venv/Scripts/pytest -v
```

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `TL_BASE_URL` | `http://localhost:8080` (в контейнере — `http://web`) | Адрес системы |
| `TL_ALLOW_DEGRADED` | пусто | Компоненты, которым разрешено «Частично», например `llm`, пока скачивается модель |
| `TL_READY_TIMEOUT` | 120 | Сколько секунд ждать готовности системы |

## Разработка

```bash
# Backend
cd backend
python -m venv .venv && .venv/Scripts/pip install uv   # Linux/macOS: .venv/bin/pip
.venv/Scripts/uv sync
.venv/Scripts/pytest
.venv/Scripts/ruff check . && .venv/Scripts/mypy app

# Frontend (dev-сервер проксирует /api на localhost:8000)
cd frontend
npm ci
npm run dev           # http://localhost:5173
npm run gen:api       # обновить контракт contracts/openapi.json и типы клиента после изменения API
npm run lint && npm run build
```

Служебные скрипты backend:

| Скрипт | Назначение |
|---|---|
| `python -m app.cli bootstrap` | Миграции и настройка Garage (выполняется контейнером `init`) |
| `python scripts/check_deps.py --browser` | Проверка совместимости зависимостей и запуска Chromium |
| `python scripts/bench_llm.py <модели…>` | Замер скорости и качества моделей Ollama |

## Версии образов

Все образы закреплены как `тег@digest` (в `docker-compose.yml` и Dockerfile) — сборка воспроизводима. Чтобы обновить образ, найдите новый тег и его digest:

```bash
docker buildx imagetools inspect postgres:18.8-alpine --format '{{json .Manifest.Digest}}'
```

## Если не запускается

| Симптом | Что проверить |
|---|---|
| `ollama` не стартует, ошибка про `nvidia` | Нет GPU или NVIDIA Container Toolkit — запустите с `docker-compose.cpu.yml` |
| `init` завершился с ошибкой | `docker compose logs init`; часто — неверные `GARAGE_*`/`S3_*` в `.env`. Формат ключа: `GK` + 24 hex, секрет — 64 hex |
| В «Состоянии» `llm` — «модель не загружена» | Модель ещё скачивается: `docker compose logs -f ollama-pull` |
| `worker` — «нет сигнала от воркера» | `docker compose logs worker` |
| UI открывается, но API отвечает 502 | `docker compose ps api` — API ещё стартует или упал |
