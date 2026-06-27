# syntax=docker/dockerfile:1
# Multi-stage build using the official uv image for fast, reproducible installs.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install dependencies first (cached unless lock/manifest change). LICENSE and
# README are needed because the project metadata references them at build time.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Install the project itself.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


# Fetch the MongoDB crypt_shared library used for automatic Queryable Encryption.
# It is only loaded when GATEWAY_ENCRYPTION_ENABLED=true, but baking it in means
# enabling encryption is a pure config flip. We use the ubuntu2204 build for both
# architectures (it has an aarch64 variant and runs fine on the bookworm glibc).
FROM debian:bookworm-slim AS crypt
ARG TARGETARCH
ARG MONGO_CRYPT_VERSION=8.0.5
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    case "$TARGETARCH" in \
      amd64) CRYPT_ARCH=x86_64 ;; \
      arm64) CRYPT_ARCH=aarch64 ;; \
      *) echo "unsupported TARGETARCH: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    url="https://downloads.mongodb.com/linux/mongo_crypt_shared_v1-linux-${CRYPT_ARCH}-enterprise-ubuntu2204-${MONGO_CRYPT_VERSION}.tgz"; \
    mkdir -p /opt/mongo/crypt_shared /tmp/crypt; \
    curl -fsSL "$url" -o /tmp/crypt.tgz; \
    tar -xzf /tmp/crypt.tgz -C /tmp/crypt; \
    cp "$(find /tmp/crypt -name 'mongo_crypt_v1.so' | head -n1)" \
       /opt/mongo/crypt_shared/mongo_crypt_v1.so; \
    rm -rf /tmp/crypt /tmp/crypt.tgz


FROM python:3.12-slim-bookworm AS runtime

# Run as an unprivileged user.
RUN groupadd --system app && useradd --system --gid app --create-home app

WORKDIR /app
COPY --from=builder --chown=app:app /app /app
COPY --from=crypt --chown=app:app /opt/mongo/crypt_shared /opt/mongo/crypt_shared

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    GATEWAY_HOST=0.0.0.0 \
    GATEWAY_PORT=8000 \
    GATEWAY_CRYPT_SHARED_LIB_PATH=/opt/mongo/crypt_shared/mongo_crypt_v1.so

USER app
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status==200 else 1)"

CMD ["blackbox-ai"]
