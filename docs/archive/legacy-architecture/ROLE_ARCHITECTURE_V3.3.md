# ZX Code v3.3 — Hardened seven-role engineering architecture

## Role topology

The seven stable role identifiers remain: Architect, Planner, Coder, Debugger,
Reviewer, Tester, and Security Auditor. Provider routing and primary-provider
fallback remain compatible with v3.2.

```text
Request -> Architect -> Planner -> Tester strategy (JSON commands)
                         |                 |
                         v                 v
                       Coder         Independent TesterRunner
                         |            (approved commands, sandbox)
                         v                 |
                    Mutation journal <-----+
                         |
                 bounded change diff
                    /           \
               Reviewer     Security Auditor
                    \           /
                 severity gate
                         |
            completed OR explicitly blocked
```

## Execution isolation

`SandboxExecutor` defaults to `mode="container"` and fails closed when Docker or
Podman is unavailable. There is no implicit host fallback. Container commands
use an argv-based runtime invocation (no host `shell=True`), read-only container
root filesystem, writable workspace-only bind mount, `/tmp` tmpfs, `--network
none` by default, dropped Linux capabilities, `no-new-privileges`, PID/memory/
CPU limits, numeric caller UID/GID where available, and a bounded timeout.
Timeout handling terminates the runtime client process group and requests
container kill/removal through its CID file. Runtime behavior still depends on
the host's Docker/Podman daemon and kernel configuration.

Host execution exists only for explicit compatibility opt-in:
`mode="host", allow_host_execution=True`. It emits a RuntimeWarning and is not
an OS sandbox. It must not be enabled for untrusted repositories or code.

Build the default toolchain image:

```sh
docker build -t zxcode-sandbox:latest -f sandbox/Dockerfile .
# or use podman build with the same arguments
```

The image includes Python, Node.js/npm, Go, Git, and common build tools. Projects
requiring other toolchains need a custom image configured as `sandbox_image`.
Network remains disabled by default, so dependency installation requiring
network access needs an explicitly reviewed `sandbox_network="bridge"` policy
(the programmatic `network` option is currently exposed by SandboxExecutor).

## Tester contract

Tester strategy output must be JSON with a `commands` string array. The
`TesterRunner` executes each command independently through the same sandbox and
approval callback, records exit status/stdout/stderr, and never treats Coder's
self-reported test claims as test evidence. Malformed or absent structured
strategy is reported as inconclusive. A supplied strategy that cannot produce
passing evidence blocks completion.

## Review gates

Reviewer and Security Auditor must return JSON containing a `findings` array.
Severity values are `critical`, `high`, `medium`, `low`, and `info`. Critical or
high findings block completion. Invalid/unstructured gate output is
`inconclusive` and blocks completion; it is never silently interpreted as a
pass. Medium and lower findings are non-blocking but remain visible. Evidence
is limited to the mutation journal's bounded, sensitive-path-filtered diff;
this is not a formal proof or complete repository audit.

## Recovery

`TaskStateStore` writes task stage, goal, plan, and selected result summaries
atomically under `.zx_checkpoints/tasks/active-task.json` with mode 0600. Its
payload allow-list excludes provider objects and credentials. `load()` validates
schema version and workspace identity and reports missing, corrupt,
incompatible, or recoverable state. Existing mutation journals/checkpoints
remain in place.

Current limitation: v3.3 persists recoverable state and exposes its loader,
but does not automatically resume an interrupted role orchestration from the
last completed stage. Conversation resume remains separate from task-state
recovery. Reviewer findings currently block rather than launching an automatic
Coder repair loop; the existing verification repair mechanism remains bounded
as implemented in v3.2. These are explicit follow-up items, not claimed features.

## Compatibility

- Seven role names and role router/provider fallback are unchanged.
- `ToolEngine` and `AgentConfig` add optional sandbox settings; container mode is
'the safe default. Existing host-only tests and trusted local workflows must
  explicitly opt in to unsafe host execution.
- The `run_command` result tuple remains `(returncode, stdout, stderr)`.
- Existing v3.2 journal, checkpoint, provider, plugin, and file-mutation APIs
  remain in place.
