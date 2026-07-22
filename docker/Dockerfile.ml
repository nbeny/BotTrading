# Image for the ML sentiment service (HuggingFace L1: transformers + torch).
# Same as the base Python image plus the heavy 'ml' extra so the CryptoBERT /
# FinBERT model actually loads instead of silently falling back to the lexicon
# scorer (scorer.py). Build context MUST be the repo root.
#
#   build args:
#     SERVICE_PATH  e.g. services/sentiment-service
#     APP_MODULE    e.g. app.main:app  (FastAPI) — used by the default CMD
#
# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Model weights are cached here; persisted via a named volume in compose so
    # they download once instead of on every cold start.
    HF_HOME=/home/appuser/.cache/huggingface

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1) Install the shared library first (best layer caching).
COPY libs/cmi_common /libs/cmi_common
RUN pip install /libs/cmi_common

# 2) Pre-install the heavy ML deps in their own cached layer so ordinary source
#    changes don't trigger a multi-GB torch re-download. CPU-only wheels keep
#    the image small (the compose stack has no GPU); drop the extra index URL to
#    pull the default CUDA build. Versions mirror the '[ml]' extra in
#    services/sentiment-service/pyproject.toml.
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu \
    "transformers>=4.42" "torch>=2.3"

# 3) Install the service and its remaining dependencies.
ARG SERVICE_PATH
COPY ${SERVICE_PATH}/pyproject.toml /app/pyproject.toml
RUN pip install -e ".[ml]" || true
COPY ${SERVICE_PATH} /app

# Drop privileges.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p ${HF_HOME} \
    && chown -R appuser:appuser /app ${HF_HOME}
ENV HOME=/home/appuser
USER appuser

ARG APP_MODULE=app.main:app
ENV APP_MODULE=${APP_MODULE}
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["sh", "-c", "uvicorn ${APP_MODULE} --host 0.0.0.0 --port 8000"]
