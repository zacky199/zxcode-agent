# MCP Protocol & Security Boundary

## Scope

ZX Code uses the official MCP Python SDK through the optional `mcp` extra. The
project's adapter package is `zxcode_mcp`; the `mcp` import namespace belongs
to the upstream SDK and must not be shadowed.

## Supported transports

- Streamable HTTP (`https://` for remote servers).
- Plain HTTP only for loopback endpoints (`localhost`, `127.0.0.1`, `::1`).
- stdio for explicitly configured local processes.

The SDK dependency is constrained to `mcp>=1.30,<2` while ZX Code retains
Python 3.10 compatibility. SDK v2 is the current upstream stable line, while
v1 receives critical security fixes; moving to v2 requires a separate Python
minimum-version and migration decision.

## Trust and invocation policy

1. Servers are configured explicitly; there is no automatic server discovery.
2. Tool allowlists are empty by default. Only configured tool names are
   registered with the model.
3. Every invocation requires a trusted approval callback. Missing callback,
   rejected approval, invalid schema, or transport failure blocks execution.
4. Tool schemas are checked as JSON Schema; arguments are validated before
   network/process execution.
5. Credentials must be supplied as configured HTTP headers, never embedded in
   endpoint URLs. Header names/values and stdio environment are bounded.
6. Tool arguments and results have size limits; timeouts are bounded.
7. Remote tool results are untrusted external data. They are returned as tool
   output, not interpreted as instructions by this adapter.
8. Remote side effects are outside the local mutation journal and cannot be
   rolled back or proven exactly-once by that journal.

## Minimal setup

```python
from zxcode_mcp import MCPManager
from core.agent import ZXAgentPro

async def approve(server_name, tool_name, arguments):
    # Replace with a trusted UI confirmation/policy implementation.
    return False  # secure default: deny until explicit approval is implemented

manager = MCPManager(approval_callback=approve)
manager.add_server(
    "docs", "https://mcp.example.com/mcp",
    allowed_tools={"search_docs"},
    headers={"Authorization": "Bearer <token-from-secret-store>"},
)
await manager.refresh_all()
# For the integrated agent API, pass the same manager to ZXAgentPro:
agent = ZXAgentPro(provider, workspace, mcp_manager=manager)
```

Install with `pip install 'zxcode-agent[mcp]'` (or `pip install -e '.[mcp]'`
from a source checkout). Do not put real tokens in source files, prompts, or
endpoint URLs.

## CLI/TUI management

The interactive CLI exposes `/mcp` commands:

- `/mcp servers` — list saved server configurations and discovery counts.
- `/mcp add-http NAME URL TOOL1,TOOL2 [--header Header-Name=ENV_VAR]` — save
  an HTTPS Streamable HTTP server with an explicit allowlist after a trust
  confirmation. Header credentials are resolved from environment variables;
  only variable names are persisted.
- `/mcp add-stdio NAME EXECUTABLE TOOL1,TOOL2 [--cwd DIR] [--docker-image IMAGE@sha256:DIGEST] -- ARG...`
  — save a local process after a separate trust confirmation. Without the
  Docker option it runs directly with host-user permissions. Docker mode
  requires `--cwd`, mounts it read-only at `/workspace`, and expects
  `EXECUTABLE` to be an absolute path inside the preloaded container image.
  The image must include the MCP server runtime and its dependencies.
- `/mcp refresh [NAME]` — connect and discover tools; this is never automatic.
- `/mcp tools` — list tools discovered during this session.
- `/mcp remove NAME` — revoke a server and its discovered tools after confirmation.

Each invocation presents the server, remote tool, and bounded argument preview;
sensitive-looking argument keys are redacted. Approval defaults to **No**.
Non-secret server configuration and environment-variable references are atomically
persisted in the user's private `~/.zxagent/mcp_servers.json` file (directory
mode 0700, file mode 0600). Secret values are resolved at runtime and never
written to the configuration file. Set the referenced variables before starting
ZX Code. Missing variables prevent that server from loading.

## Explicit limitations

- HTTP credentials may use environment-variable references or the optional OS
  credential vault. Environment variables must be protected by the operating
  system and shell/session policy. Vault secrets are resolved only at runtime;
  vault names, not values, are persisted.
- HTTP endpoints are resolved immediately before transport creation; any
  private, loopback (except explicit loopback endpoints), link-local, reserved,
  or otherwise non-global DNS answer is rejected. The returned address set is
  pinned at the TCP network-backend layer for every connection. TLS SNI,
  certificate verification, and the HTTP Host header retain the original URL
  hostname. Environment proxies are disabled so they cannot bypass the pin.
  If the installed httpx/httpcore internals do not expose the expected backend
  hook, the transport fails closed rather than reverting to ordinary DNS.
  Redirect following is disabled; HTTP connection retries are disabled and the
  transport limits concurrent connections to four (two keep-alive) with a 15s
  idle expiry. These controls limit destination drift and resource abuse but do
  not create a host firewall. Host-level egress filtering is not implemented or
  applied by ZX Code; operators should enforce it separately (for example, a
  dedicated user/container network policy allowing only approved destinations).
- stdio now requires an absolute path resolving to a regular executable, a
  valid working directory, bounded arguments, and rejects common dynamic
  loader/runtime injection environment variables. Child processes receive
  the SDK's documented small baseline allowlist (`HOME`, `LOGNAME`, `PATH`,
  `SHELL`, `TERM`, `USER`) plus explicitly configured variables. It does not
  inherit the full ZX Code environment. This is environment minimization,
  not an OS sandbox:
  the process still runs as the ZX Code user and can access that user's files,
  credentials, and network. Treat the executable and its dependencies as
  fully trusted.
- MCP calls are external effects; a timeout does not prove the remote action
  did not complete.


## stdio process isolation

- Direct stdio remains available for compatibility and is explicitly **not**
  an OS sandbox. Its process runs with the ZX Code user's OS permissions.
- An opt-in Docker mode is selected with `--docker-image IMAGE@sha256:DIGEST`
  and requires an explicit `--cwd` host directory. The image must already be
  present locally (`--pull=never`); ZX Code does not pull untrusted images.
- The container runs without network access, with a read-only root filesystem,
  all Linux capabilities dropped, `no-new-privileges`, Docker's default
  seccomp/AppArmor policy, 64-process limit, 256 MiB memory/swap cap, 1 CPU,
  and a 64 MiB noexec/nosuid/nodev `/tmp` tmpfs. Core dumps are disabled,
  file descriptors are capped at 1024, and shared memory at 16 MiB. The image
  entrypoint is overridden so only the configured server executable runs. The selected host working
  directory is mounted read-only at `/workspace`; no home directory or Docker
  socket is mounted. The container runs under the invoking host UID/GID.
- The digest pin prevents mutable tag drift. Operators remain responsible for
  obtaining and auditing images. A compromised Docker daemon or host kernel is
  outside this boundary. The Docker CLI/daemon must be installed and available.
- Direct mode must not be described as isolated. For untrusted third-party
  stdio servers, use Docker mode and a minimal, audited image. The base image
  must include the MCP server runtime and dependencies; the Docker mode does
  not install packages or fetch images automatically.

## OS credential vault (optional extra)

HTTP MCP credentials can be stored in the operating system's credential service
using the optional `keyring` dependency. The vault refuses unknown/plaintext
backends and does not write secret values to `mcp_servers.json`. Store a secret
interactively with `/mcp vault-set NAME`; the value is hidden at the prompt.
Delete it with `/mcp vault-delete NAME` after confirmation. Register a header
reference with `--vault Authorization=NAME` on `/mcp add-http`. Environment
variable references (`--header Authorization=ENV_NAME`) remain supported.

The backend detection is intentionally conservative. Linux desktop SecretService
requires an unlocked user session/keyring; headless systems may not have a secure
backend configured. VM verification currently detects an unsupported generic
keyring backend and confirms the vault fails closed; the native SecretService
integration is therefore not verified on this headless VM. On those systems do not
replace it with plaintext files or shell history. Windows Credential Vault and
macOS Keychain are supported only when the installed Python keyring selects their
secure backend; those platforms have not been tested in this release workspace.
