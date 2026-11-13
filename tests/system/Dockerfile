# syntax=docker/dockerfile:1.7
# Контекст сборки — корень репозитория (нужен contracts/openapi.json).
FROM python:3.14.10-slim-trixie@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f
COPY --from=ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 /uv /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    TL_CONTRACT_PATH=/contracts/openapi.json

WORKDIR /tests
COPY tests/system/pyproject.toml tests/system/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project
COPY tests/system/ ./
COPY contracts /contracts

ENTRYPOINT ["pytest"]
CMD ["-v"]
