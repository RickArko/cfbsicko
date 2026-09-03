# syntax=docker/dockerfile:1.7
ARG PYTHON_VERSION=3.13
ARG UV_VERSION=0.11.17
ARG NODE_VERSION=22

FROM node:${NODE_VERSION}-bookworm-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gosu \
    && rm -rf /var/lib/apt/lists/*

FROM base AS builder
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable || uv sync --no-dev --no-editable

FROM base AS runtime
ENV HOST=0.0.0.0 \
    PORT=8000 \
    DATABASE_PATH=/data/locks.db \
    DATABASE_TYPE=sqlite \
    WEB_AUTH_ENABLED=true \
    REQUIRE_EMAIL_CONFIRMED=true \
    CFBSICKO_FRONTEND_DIST=/app/frontend/dist
WORKDIR /app
RUN groupadd --system --gid 1001 app \
    && useradd --system --uid 1001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown app:app /data /app
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /build/src /app/src
COPY --from=frontend /fe/dist /app/frontend/dist
# Also sit next to the installed package (uv --no-editable lives in site-packages).
COPY --from=frontend /fe/dist /opt/venv/lib/python3.13/site-packages/cfbsicko/static
COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/docker-entrypoint.sh
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl --fail --silent --show-error http://127.0.0.1:8000/api/health || exit 1
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["cfbsicko", "--host", "0.0.0.0", "--port", "8000"]
