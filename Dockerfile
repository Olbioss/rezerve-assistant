# syntax=docker/dockerfile:1
# A two-stage uv build: dependencies resolve from the lockfile in a cached
# layer of their own, and the final image holds just the virtualenv and code.
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY README.md alembic.ini ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable

FROM python:3.13-slim
RUN useradd --system --uid 10001 assistant
WORKDIR /app
COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYDANTIC_AI_NO_BANNER=1
USER assistant
EXPOSE 8000
# Cloud Run says which port in $PORT; locally it is 8000.
CMD ["sh", "-c", "exec uvicorn assistant.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}"]
