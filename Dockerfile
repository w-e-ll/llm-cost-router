FROM ghcr.io/astral-sh/uv:0.11.7 AS uv
FROM python:3.13-slim-bookworm AS base
COPY --from=uv /uv /uvx /bin/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app
COPY llm_cost_router ./llm_cost_router
COPY migrations ./migrations
COPY alembic.ini VERSION.txt ./
RUN useradd --uid 10001 --create-home app && mkdir -p /app/var/logs && chown -R app:app /app/var
USER app
FROM base AS test
USER root
RUN uv sync --frozen --no-install-project
COPY tests ./tests
COPY scripts ./scripts
USER app
CMD ["uv", "run", "--no-sync", "pytest", "-q", "-p", "no:cacheprovider"]
FROM base AS runtime
CMD ["uvicorn", "llm_cost_router.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
