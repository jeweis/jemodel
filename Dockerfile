FROM python:3.12-slim-bookworm AS backend
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.5.20 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --frozen || uv sync --no-dev
COPY app ./app
COPY scripts ./scripts
COPY frontend_build ./frontend_build
ENV JEMODEL_STATIC_DIR=/app/frontend_build \
    JEMODEL_DATABASE_URL=sqlite:////data/jemodel.db \
    PYTHONPATH=/app
VOLUME ["/data"]
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
