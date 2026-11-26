#!/usr/bin/env bash
# Build the MCP protocol fixture against the local pinned sandbox base and run real Docker E2E.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$HOME/.local/bin:$PATH"
FIXTURE="$ROOT/tests/fixtures/mcp_stdio_e2e"
BASE_REF="zxcode-sandbox:full@sha256:7fabb8687917bdd2c2ef593ad0824f31ae83325da5545ffd605e41da0f4794ae"
IMAGE_TAG="zxcode-mcp-e2e:local"
if ! docker image inspect "$BASE_REF" >/dev/null 2>&1; then
  echo "Pinned sandbox base image missing locally: $BASE_REF" >&2
  echo "Build/verify the sandbox image first; this runner will not pull or substitute an image." >&2
  exit 2
fi
docker build --pull=false -t "$IMAGE_TAG" "$FIXTURE"
IMAGE_DIGEST="$(docker image inspect --format '{{index .RepoDigests 0}}' "$IMAGE_TAG")"
if [[ -z "$IMAGE_DIGEST" || "$IMAGE_DIGEST" == "<no value>" ]]; then
  echo "Docker did not return a digest reference for the fixture image." >&2
  exit 3
fi
cd "$ROOT"
ZXCODE_MCP_DOCKER_IMAGE="$IMAGE_DIGEST" uv run --offline --extra dev --extra mcp pytest -q tests/test_mcp_docker_e2e.py
