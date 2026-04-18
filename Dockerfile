# ── Build stage ────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements_hl.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements_hl.txt


# ── Runtime stage ──────────────────────────────────────────────────────────
FROM python:3.11-slim

LABEL maintainer="CryptoBOT" \
      description="Hyperliquid perps trading bot"

# Non-root user for security
RUN useradd -m -u 1000 botuser

WORKDIR /app

# Copy installed packages from build stage
COPY --from=builder /install /usr/local

# Copy bot source
COPY bot/          ./bot/
COPY data/         ./data/

# data dir must be writable (volume mount will override this)
RUN mkdir -p /app/data && chown -R botuser:botuser /app

USER botuser

# Health check: verifies the process is alive
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import os, time; \
        st = os.path.getmtime('data/state.json') if os.path.exists('data/state.json') else 0; \
        exit(0 if (time.time() - st) < 180 else 1)"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

ENTRYPOINT ["python", "-m", "bot.main"]
