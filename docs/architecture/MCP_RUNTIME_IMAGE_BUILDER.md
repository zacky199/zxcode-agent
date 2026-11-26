# MCP Runtime Image Builder and End-to-End Verification

This fixture provides a reproducible, digest-pinned MCP stdio runtime for
integration testing. It is intentionally separate from production image
publishing: it does not publish images, modify the user's Docker daemon, or
install an MCP server on the host.

## Build

Prerequisites:

- Linux host with Docker CLI and daemon.
- Locally built `zxcode-sandbox:full` image matching the pinned base digest
  in `tests/fixtures/mcp_stdio_e2e/Dockerfile`.
- Network access during the image build to retrieve the exact Python wheels in
  `requirements.lock`. The runtime container itself has no network access.

Run from the repository root:

```bash
./scripts/build_mcp_e2e_image.sh
```

The script refuses to pull the base image, builds the fixture, and prints the
immutable digest reference. The test image contains MCP SDK `1.30.0` and
exactly pinned direct/transitive Python dependency versions. Update that lock
only as a reviewed dependency change. Version pinning does not replace
artifact hash verification; for a supply-chain hardened release, add a
platform-specific hash lock and verify provenance/signatures before publishing.

## End-to-end test

Set the exact digest printed by the builder:

```bash
ZXCODE_MCP_DOCKER_IMAGE='zxcode-mcp-e2e@sha256:...' \
  python -m pytest -q tests/test_mcp_docker_e2e.py -vv
```

The test uses the production `MCPClient` and `build_docker_stdio_command`
path. It performs MCP initialize, tool discovery, a real tool invocation,
and a second tool invocation that probes isolation from inside the server
process. It asserts:

- MCP `echo` tool round-trip succeeds.
- Container root filesystem is not writable.
- The mounted `/workspace` is not writable.
- The bounded `/tmp` tmpfs remains writable.
- External network access is blocked and the Docker socket is not visible.
- Effective Linux capabilities are zero and `no_new_privileges` is active.
- The process, memory, CPU, file-descriptor, and core-dump limits match the
  configured container policy.
- `/tmp` mount options include `noexec`, `nosuid`, and `nodev`; `/tmp` and
  `/dev/shm` sizes stay within their configured bounds.

The test is opt-in and is skipped in ordinary unit-test runs unless
`ZXCODE_MCP_DOCKER_IMAGE` is set and Docker is available. This prevents a
regular test run from depending on a local Docker daemon or a mutable image tag.

## Security boundary

This validates the configured Docker restrictions on the current Linux/Docker
host. It does not prove resistance to a compromised Docker daemon, host kernel,
container runtime, or malicious base-image build. The fixture image is for
integration testing, not a general-purpose production MCP server catalog.
