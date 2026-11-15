FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    EMBERLENS_ENV=production \
    EMBERLENS_DB=/app/data/emberlens.sqlite

LABEL org.opencontainers.image.title="EmberLens" \
      org.opencontainers.image.version="0.8.0"

WORKDIR /app

COPY requirements-prod.txt ./requirements-prod.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --upgrade pip \
    && python -m pip install -r requirements-prod.txt

COPY backend ./backend
COPY frontend ./frontend
COPY scripts ./scripts
COPY README.md ./README.md
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh

RUN useradd --create-home --uid 10001 emberlens \
    && mkdir -p /app/data \
    && chown -R emberlens:emberlens /app

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/ready', timeout=3).read()" || exit 1

CMD ["sh", "-c", "exec uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-1}"]
