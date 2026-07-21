# Generic multi-stage Dockerfile shared by every CMI service.
# Build context MUST be the repo root so the shared library is available.
#
#   build args:
#     SERVICE_PATH  e.g. services/collector-coingecko
#     APP_MODULE    e.g. app.main:app  (FastAPI) — used by the default CMD
#
# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1) Install the shared library first (best layer caching).
COPY libs/cmi_common /libs/cmi_common
RUN pip install /libs/cmi_common

# 2) Install the service and its dependencies.
ARG SERVICE_PATH
COPY ${SERVICE_PATH}/pyproject.toml /app/pyproject.toml
RUN pip install -e . || true
COPY ${SERVICE_PATH} /app

# Optionally install the Claude CLI (only for the AI worker images).
ARG INSTALL_CLAUDE_CLI=false
RUN if [ "$INSTALL_CLAUDE_CLI" = "true" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends nodejs npm \
        && rm -rf /var/lib/apt/lists/* \
        && npm install -g @anthropic-ai/claude-code; \
    fi

# Drop privileges.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
ENV HOME=/home/appuser
USER appuser

ARG APP_MODULE=app.main:app
ENV APP_MODULE=${APP_MODULE}
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# FastAPI services use uvicorn; pure workers override CMD in compose.
CMD ["sh", "-c", "uvicorn ${APP_MODULE} --host 0.0.0.0 --port 8000"]
