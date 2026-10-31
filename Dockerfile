# Multi-stage Dockerfile for TodayTech
# Builds a lightweight, non-root container for running the agent, bot, or scheduler.

# ─────────────────────────────────────────────────────────────
# Stage 1: Build & Dependencies
# ─────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install build backend and dependencies
COPY pyproject.toml README.md /build/
RUN pip install --no-cache-dir --upgrade pip build wheel \
    && pip wheel --no-cache-dir --no-deps --wheel-dir /build/wheels -e .

# ─────────────────────────────────────────────────────────────
# Stage 2: Production Runtime
# ─────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runner

# Create non-root user and group
RUN groupadd -g 1001 todaytech \
    && useradd -u 1001 -g todaytech -m -s /bin/bash todaytech

WORKDIR /app

# Install runtime dependencies (e.g. libxml2 for lxml if needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed wheel and project files
COPY pyproject.toml README.md /app/
COPY todaytech/ /app/todaytech/
COPY sources/ /app/sources/
COPY sources.yaml /app/sources.yaml

# Install project and dependencies
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e .

# Ensure data directory exists and has correct permissions
RUN mkdir -p /app/data /app/data/drafts \
    && chown -R todaytech:todaytech /app

USER todaytech

# Environment defaults
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TODAYTECH_DATA_DIR=/app/data \
    TODAYTECH_PROFILE=/app/profile.yaml \
    TODAYTECH_SOURCES=/app/sources.yaml

# Healthcheck checking the database file or process
HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "from todaytech.core.config import Settings; Settings()" || exit 1

EXPOSE 8000

ENTRYPOINT ["todaytech"]
CMD ["web"]
