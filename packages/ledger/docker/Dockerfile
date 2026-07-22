# syntax=docker/dockerfile:1.7

# ---- builder -----------------------------------------------------------
FROM python:3.14-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml ./
COPY packages/python ./packages/python
COPY packages/ledger ./packages/ledger
RUN pip install --prefix=/install ./packages/python "./packages/ledger[kms]"

# ---- runtime -----------------------------------------------------------
FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LEDGER_LISTEN_HOST=0.0.0.0 \
    LEDGER_LISTEN_PORT=8443

RUN apt-get update && apt-get install -y --no-install-recommends \
        tini libpq5 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/false --uid 10001 ledger

COPY --from=builder /install /usr/local
COPY packages/ledger/alembic.ini /app/alembic.ini

WORKDIR /app
USER ledger

EXPOSE 8443
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; \
    sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8443/health',timeout=2).status==200 else 1)" || exit 1

ENTRYPOINT ["tini", "--", "ledger"]
CMD ["serve"]
