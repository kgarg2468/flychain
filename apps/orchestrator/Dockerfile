# syntax=docker/dockerfile:1.7
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.4.20

WORKDIR /app

COPY pyproject.toml ./
COPY apps/orchestrator/pyproject.toml ./apps/orchestrator/pyproject.toml
COPY apps/gateway/pyproject.toml ./apps/gateway/pyproject.toml
COPY packages/sdk-py/pyproject.toml ./packages/sdk-py/pyproject.toml
COPY packages/capability-compiler/pyproject.toml ./packages/capability-compiler/pyproject.toml

COPY apps/orchestrator ./apps/orchestrator
COPY apps/gateway ./apps/gateway
COPY packages/sdk-py ./packages/sdk-py
COPY packages/capability-compiler ./packages/capability-compiler

RUN uv pip install --system ./packages/sdk-py ./packages/capability-compiler ./apps/gateway ./apps/orchestrator

CMD ["arq", "flychain_orchestrator.worker.WorkerSettings"]
