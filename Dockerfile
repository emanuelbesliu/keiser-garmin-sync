# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    KGS_DATA_DIR=/data

WORKDIR /app

# Install the package (with the optional web server) using only the metadata
# first, for better layer caching.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[server]"

RUN adduser --disabled-password --gecos '' --uid 1000 appuser \
    && mkdir -p /data && chown -R appuser:appuser /data /app
USER appuser

EXPOSE 8096

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8096/health')"

ENTRYPOINT ["keiser-garmin-sync"]
# Default to the long-running service; override with e.g. `docker run ... sync`.
CMD ["serve", "--host", "0.0.0.0", "--port", "8096"]
