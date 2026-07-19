# syntax=docker/dockerfile:1.6
# ============================================================
# Institutional Crypto Futures Intelligence Platform
# Multi-stage production Dockerfile
# ============================================================

ARG PYTHON_VERSION=3.12

# ---------- Stage 1: Builder ----------
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Install build deps for asyncpg / numpy
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create venv and install requirements
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ---------- Stage 2: Runtime ----------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app"

# Runtime deps (libpq for asyncpg, curl for healthcheck, tini for PID 1)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r platform && useradd -r -g platform platform

# Copy venv from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application code
WORKDIR /app
COPY --chown=platform:platform app/ ./app/
COPY --chown=platform:platform main.py .
COPY --chown=platform:platform requirements.txt pytest.ini ./
COPY --chown=platform:platform .env.example ./
COPY --chown=platform:platform scripts/ ./scripts/
COPY --chown=platform:platform deploy/ ./deploy/
COPY --chown=platform:platform tests/ ./tests/
COPY --chown=platform:platform docs/ ./docs/
COPY --chown=platform:platform README.md .

# Ensure entrypoint is executable
RUN chmod +x /app/deploy/entrypoint.sh /app/main.py

# Create data directory for SQLite / logs
RUN mkdir -p /app/data /app/logs && chown -R platform:platform /app

USER platform

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS http://localhost:8080/health || exit 1

# Use tini as PID 1 for proper signal handling (SIGTERM / SIGINT),
# then run our entrypoint (DB migrations + startup notify), then the app.
ENTRYPOINT ["/usr/bin/tini", "--", "/app/deploy/entrypoint.sh"]

# Default: run 24/7 pipeline via main.py
CMD ["python", "main.py"]
