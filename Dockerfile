# Multi-stage production build for CloudPredict
FROM python:3.12-slim AS builder

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Install uv for fast, deterministic dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Copy project specification files
COPY pyproject.toml uv.lock README.md ./

# Install dependencies into virtual environment
RUN uv sync --frozen --no-dev

# Final runtime stage
FROM python:3.12-slim AS runner

WORKDIR /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    CLOUDPREDICT_ENV=production

# Create non-root system user for enterprise security
RUN groupadd -r cloudpredict && useradd -r -g cloudpredict cloudpredict

# Copy installed venv and project code
COPY --from=builder /app/.venv /app/.venv
COPY src /app/src
COPY config /app/config
COPY pyproject.toml README.md ./

# Create data directories with appropriate permissions
RUN mkdir -p /app/data/models /app/data/report /app/data/real_trace \
    && chown -R cloudpredict:cloudpredict /app

USER cloudpredict

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/status')" || exit 1

ENTRYPOINT ["uvicorn", "cloudpredict.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
