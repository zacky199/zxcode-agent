#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_IMAGE="zxcode-sandbox:full@sha256:7fabb8687917bdd2c2ef593ad0824f31ae83325da5545ffd605e41da0f4794ae"
IMAGE_TAG="${ZXCODE_MCP_E2E_TAG:-zxcode-mcp-e2e:1.30.0}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker CLI is required" >&2
  exit 1
fi
if ! docker image inspect "$BASE_IMAGE" >/dev/null 2>&1; then
  echo "Required base image is not present locally: $BASE_IMAGE" >&2
  echo "Build the ZX Code full sandbox image first; this script never pulls it." >&2
  exit 1
fi

docker build --pull=false \
  --file "$ROOT/tests/fixtures/mcp_stdio_e2e/Dockerfile" \
  --tag "$IMAGE_TAG" \
  "$ROOT/tests/fixtures/mcp_stdio_e2e"

IMAGE_DIGEST="$(docker image inspect "$IMAGE_TAG" --format '{{index .RepoDigests 0}}')"
if [[ -z "$IMAGE_DIGEST" || "$IMAGE_DIGEST" == "<no value>" ]]; then
  echo "Docker did not return an immutable image digest" >&2
  exit 1
fi
printf '\nBuilt MCP test image: %s\n' "$IMAGE_DIGEST"
printf 'Run integration test:\n'
printf 'ZXCODE_MCP_DOCKER_IMAGE=%q %q -m pytest -q %q\n' \
  "$IMAGE_DIGEST" "${PYTHON:-python}" "$ROOT/tests/test_mcp_docker_e2e.py"
