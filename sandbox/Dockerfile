# Default image is the lean Python/Node/Go runtime.
# Build default: docker build -t zxcode-sandbox:latest -f sandbox/Dockerfile .
# Optional native C/C++ toolchain: docker build --target full -t zxcode-sandbox:full -f sandbox/Dockerfile .
# The image is not the security boundary; execution/sandbox.py supplies the
# runtime isolation flags (read-only rootfs, no network, dropped capabilities).

FROM node:22-bookworm-slim AS node-toolchain
FROM golang:1.24-bookworm AS go-toolchain

FROM python:3.12-slim-bookworm AS base

ENV PATH=/usr/local/go/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin \
    GOPATH=/tmp/go \
    GOCACHE=/tmp/go-cache \
    GOMODCACHE=/tmp/go/pkg/mod \
    npm_config_cache=/tmp/npm-cache \
    PIP_CACHE_DIR=/tmp/pip-cache \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Do not install Debian golang-go/nodejs/npm: official upstream toolchain
# stages avoid the large Debian source packages and duplicate dependencies.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       bash ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*

COPY --from=node-toolchain /usr/local/bin/ /usr/local/bin/
COPY --from=node-toolchain /usr/local/lib/node_modules/ /usr/local/lib/node_modules/
COPY --from=go-toolchain /usr/local/go/ /usr/local/go/

# Optional heavy native compilation profile. Keep it out of the default image.
FROM base AS full
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/*

# Last stage is intentionally the default target to keep normal pulls smaller.
FROM base AS runtime
WORKDIR /workspace
CMD ["sh"]
