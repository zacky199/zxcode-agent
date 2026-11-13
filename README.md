<div align="center">

# ⚡ ZX Code

### Professional Coding Agent CLI

*An opencode-style autonomous coding agent for your terminal — it plans, explores your workspace, edits files, runs commands, and verifies its own work.*

![version](https://img.shields.io/badge/version-3.5.6-blue)
![python](https://img.shields.io/badge/python-3.10%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)
![themes](https://img.shields.io/badge/themes-9-9cf)
![providers](https://img.shields.io/badge/providers-multi--AI-orange)

[**Features**](#-features) · [**Install**](#-install) · [**Quick start**](#-quick-start) · [**Multi-AI routing**](#-multi-ai-routing) · [**Security**](#-security-boundaries) · [**Changelog**](CHANGELOG.md)

</div>

---

ZX Code (`zx`) runs a checkpointed ReAct loop behind a distinctive **Aurora** terminal UI. It is built for reviewable, recoverable work: native file edits are opt-in, shell commands are individually approved and sandboxed by default, and every mutation is journaled so interrupted tasks can be inspected and resumed safely.

## 📑 Contents

- [Features](#-features)
- [Install](#-install)
- [Sandbox image](#-sandbox-image)
- [Quick start](#-quick-start)
- [Multi-AI routing](#-multi-ai-routing)
- [Skills](#-skills)
- [Plugin SDK](#-plugin-sdk)
- [Security boundaries](#-security-boundaries)
- [Configuration](#%EF%B8%8F-configuration)
- [Development](#-development)
- [Roadmap](#%EF%B8%8F-roadmap)
- [Release notes](#-release-notes)

## ✨ Features

### 🧠 Agent core

- **Native tool calling** — workspace inspection, approved sandboxed commands, search, glob, git status/diff, and parallel sub-agent delegation via OpenAI-style function calling, with a markdown-protocol fallback for routers that don't support tool calling. Native file writes and edits (`write_file`/`edit_file`) are **opt-in**: off by default, enabled by you with `--allow-edits` or `/edits on`.
- **Smarter, cheaper turns** — the planner is skipped for greetings and short questions (`--plan auto|always|never`); several read-only tool calls in one turn run in parallel; huge command output is cut to head + tail before it reaches the model; `read_file` takes `start_line`/`end_line` for big files; `run_command` takes a `timeout` (up to 900 s) for slow builds.
- **Hardened ReAct loop**
  - Exponential-backoff retry on transient network/provider errors.
  - Auto context compaction when the token budget is nearly exhausted; the summary keeps the requests, the tools used and the failures.
  - Anti-infinite-loop guard for repeated identical tool calls.
  - Task-scoped journals for mutation reporting and recovery records; they do not sandbox processes.
  - Verification failures after successful edits trigger one bounded, evidence-based repair pass; repeated failure stops without erasing successful edits.

### 🗂️ Workspace & sessions

- **Project-aware** — detects the language, JS package manager, frameworks and the commands that verify work (`pytest`, `pnpm test`, `cargo test`, ...), and loads `ZX.md` / `AGENTS.md` / `CLAUDE.md` from the project root as guidance (treated as untrusted text, capped at 8,000 characters).
- **Resumable sessions** — the conversation is saved per folder (`~/.zxagent/sessions`, private files) and restored with `--continue` or `/resume`.
- **Memory & indexing** — project metadata, semantic store, tree-sitter symbol index, and a repo symbol map injected into the model context.

### 🔐 Approvals & sandbox

- **Reviewable approvals** — the shell approval prompt lists risk notes (destructive patterns, credential-like paths, pipe-to-shell, system paths).
- **OS-isolated command execution** — Docker/Podman is required by default; commands run with a read-only container root, workspace-only writable mount, network disabled, dropped capabilities, no-new-privileges, and resource limits. If no runtime is available, commands fail closed. Host execution requires both `--sandbox-mode host` and `--allow-host-execution` and is explicitly unsafe.

### ♻️ Crash recovery

- **Crash-recovery state** — task stages are atomically persisted under `.zx_checkpoints/tasks` with credentials excluded from the state payload. Durable node-boundary resume checks a bounded SHA-256 workspace fingerprint; changed or un-fingerprintable workspaces are blocked for manual reconciliation. Running nodes are never replayed automatically. Inspect saved tasks with `/tasks`, inspect a checkpoint with `/task-status <task-id>`, run a read-only diagnostic with `/recovery-doctor [task-id]`, discard only its metadata with `/discard-task <task-id>`, and continue a safe pending graph with `/resume-task <task-id>`. The Recovery Doctor never repairs or resumes a task; historical checkpoints support aggregate workspace drift detection, not per-file conflict attribution.

### 👥 Workflow roles & delegation

- **Seven-role engineering workflow** — Architect, Planner, Coder, Debugger, Reviewer, Tester, and Security Auditor have explicit stage contracts. Tester can return structured commands for a separate verification runner; Reviewer and Security Auditor return structured severity findings. Critical/high or inconclusive review findings block completion.
- **Sub-agents** — delegate independent tasks to parallel agent loops that share the workspace.

### 🎨 UI & extensibility

- **Built-in skills** — UI-UX Pro Max, Frontend/Backend/Python Expert, Debugger, Code Reviewer, Git Workflow, Security Auditor, Refactor Pro, Test Writer. Install more from GitHub/GitLab-hosted sources.
- **Plugin SDK** — trusted Python plugins add tools, lifecycle hooks and event observation.
- **9 terminal themes** — aurora (default), nebula, nord, dracula, monokai, night_owl, one_dark, sakura, matrix — with a live activity panel.

## 📦 Install

```bash
pip install -r requirements.txt
python main.py --version
```

Requires Python 3.10+.

## 🐳 Sandbox image

Build the default lean toolchain image once before running command tools:

```bash
docker build -t zxcode-sandbox:latest -f sandbox/Dockerfile .
# Podman users can use: podman build -t zxcode-sandbox:latest -f sandbox/Dockerfile .
```

The default image includes Python, Node.js/npm, Go, Git, curl, and a shell. It intentionally omits C/C++ compilers to keep the normal image smaller. For projects that compile native extensions or C/C++, build the optional full image:

```bash
docker build --target full -t zxcode-sandbox:full -f sandbox/Dockerfile .
```

Select it with `--sandbox-image zxcode-sandbox:full`. The container has no network by default. Do not enable host mode for untrusted repositories. Access to the Docker group is effectively root-equivalent on the host; grant it only to trusted operators.

## 🚀 Quick start

```bash
# Run inside a project folder
python main.py --dir /path/to/project
```

On first launch you'll be asked to configure a provider/API key, or run the wizard any time with `/setup`.

### Command-line options

| Flag            | Meaning                                  |
|-----------------|------------------------------------------|
| `--dir DIR`     | Workspace/project folder                 |
| `--setup`       | Run the API configuration wizard         |
| `--provider ID` | Provider id (deepseek, claude, ...)      |
| `--model NAME`  | Override the model name                  |
| `--theme NAME`  | UI theme (default `aurora`)              |
| `--skill NAME`  | Activate a skill folder on start         |
| `--allow-edits` | Enable native file edits (asks to confirm; off by default) |
| `-c`, `--continue` | Resume the saved conversation for this folder |
| `--max-iters N` | Tool-use iterations per request (default 30, range 1-200) |
| `--plan MODE`   | `auto` (default), `always` or `never` run the planner |
| `--sandbox-mode MODE` | `container` (default) or explicitly unsafe `host` |
| `--allow-host-execution` | Required second opt-in for unsafe host shell |
| `--sandbox-runtime PATH` | Explicit Docker/Podman executable (otherwise auto-detected) |
| `--sandbox-image IMAGE` | Sandbox image (default `zxcode-sandbox:latest`) |
| `--sandbox-network MODE` | `none` (default) or `bridge` |
| `--version`     | Print version                            |

### Interactive commands

| Command         | Action                                      |
|-----------------|---------------------------------------------|
| `/help`         | Show the command menu                       |
| `/model [name]` | Switch model or list models                 |
| `/provider`     | List / switch AI provider                   |
| `/multi`        | Configure multi-AI routing (roles)          |
| `/skill`        | Load a skill persona                        |
| `/skills`       | List all skills                             |
| `/install <url>`| Install a skill/plugin                      |
| `/theme [name]` | Change UI theme                             |
| `/setup`        | Reconfigure provider / API key / model      |
| `/confirm`      | Disable legacy auto-approval; commands still need individual approval |
| `/edits [on\|off]` | Show or change native file-edit access for this session |
| `/resume`       | Reload the saved conversation for this folder |
| `/tasks`        | List persisted task metadata and recovery state |
| `/task-status <task-id>` | Inspect checkpoint, DAG node acceptance, and recovery evidence |
| `/recovery-doctor [task-id]` | Read-only recovery diagnostics (dry-run; no repair or resume) |
| `/discard-task <task-id>` | Delete selected checkpoint metadata after confirmation (workspace is untouched) |
| `/new`          | Start a fresh conversation (deletes the saved copy) |
| `/session`      | Show session stats                          |
| `/clear`        | Clear screen (keeps memory)                 |
| `/exit`         | Quit                                        |

### Providers

The agent talks to any OpenAI-compatible `/chat/completions` endpoint plus the Anthropic Messages API: DeepSeek, Anthropic Claude, OpenAI, Groq, Gemini (shim), MiniMax, Ollama, and the Indonesian routers **NaraRouter** and **LapakRouter**.

## 🔀 Multi-AI routing

```bash
/multi
```

Use `/multi` to assign models to seven workflow roles:

| Role | Active responsibility |
|---|---|
| Architect | Produces a design/constraint brief before planning |
| Planner | Builds the ordered task graph |
| Coder | Runs the tool-enabled implementation loop |
| Debugger | Supplies repair analysis after verification failures |
| Reviewer | Independently reviews the bounded change diff |
| Tester | Produces a verification strategy that the Coder must execute |
| Security Auditor | Independently inspects the change diff for security risks |

Architect and Tester are optional advisory stages. Reviewer and Security Auditor run only when configured with a model different from the primary model; this avoids calling a same-model answer an independent review. Every role falls back safely to the primary provider when no dedicated provider is configured. Role outputs are surfaced as `role_result` events for the terminal UI.

## 🧩 Skills

```bash
/skills      # list all
/skill       # pick a persona
/install https://github.com/user/repo   # install a new skill
```

Skills ship as a folder containing `manifest.json` + `SKILL.md`. Built-in skills are read-only and shipped in `skills/`; installed skills go to `~/.zxagent/skills/`.

## 🔌 Plugin SDK

Drop a trusted Python file into `~/.zxagent/plugins/` to extend the agent. Project-local `.zxagent/plugins/` files are not auto-loaded. A plugin can register custom tools, hook lifecycle events and transform prompts. Before activation, the CLI displays the complete source and SHA-256 and asks for approval each session:

```python
from plugins.sdk import BasePlugin, PluginContext

class GreeterPlugin(BasePlugin):
    name = "greeter"
    version = "1.0.0"

    def get_tools(self):
        return [{
            "type": "function",
            "function": {
                "name": "greet",
                "description": "Greet a name.",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
        }]

    def execute_tool(self, name, args):
        if name == "greet":
            return f"Hello, {args.get('name')}!"
```

See `plugins/sdk.py` for the full API (`initialize`, `shutdown`, `on_event`, `transform_user_prompt`, `PluginContext`).

## 🛡️ Security boundaries

- **Native file edits are opt-in and off by default.** Only you can enable them: start with `--allow-edits` or type `/edits on` (both ask for confirmation; the setting lasts for the session and is not saved). The switch lives on the tool engine and is never read from model arguments or Markdown text. While it is off, `write_file`/`edit_file` are not offered to the model, and any call that still arrives (including Markdown `file`/`patch` blocks) fails closed before a checkpoint is taken or a file is touched. While it is on, edits stay inside the workspace, credential-like paths remain blocked, every edit goes through the task journal, and `write_file` does not create missing directories (create them first with an approved `mkdir -p`). Turning edits on does not sandbox anything.
- **Project instructions are untrusted input.** `ZX.md`/`AGENTS.md`/`CLAUDE.md` come from the folder you open, so they are labelled as guidance that cannot override safety rules or approval prompts. Symlinked, hard-linked, credential-like and out-of-workspace files are ignored. A hostile repository can still try to steer the model; review commands before approving them.
- **Saved sessions contain what you typed.** They live in `~/.zxagent/sessions` (directory `0700`, files `0600`), never in the workspace; `/new` deletes the copy.
- **Approval risk notes are advice, not a boundary.** A command without notes is not thereby safe; every shell command still needs your explicit approval.
- The task journal and checkpoints are recovery records, **not an OS sandbox**: they do not confine a process or limit its permissions. Shell commands run with ZX Code's same OS permissions, are not limited to the workspace, and require individual approval. `/confirm` cannot bypass a per-command prompt; approval does not create isolation. Shell workspace deltas remain ambiguous and are never automatically rolled back.
- A shell before/after workspace scan cannot establish which process authored a delta. Shell changes are therefore always reported as ambiguous and are never automatically rolled back, even after a failed command; successful commands also warn when they leave changed paths. If the post-scan fails, the task summary marks the workspace state `CONFLICT/UNCERTAIN` and does not infer that there was no delta. Shell pre-state scans all non-sensitive workspace paths, including hidden and generated trees, apart from the private journal directory. Credential-like files are never copied; symlink/special-file changes are reported rather than automatically restored, and hard-linked files fail closed. The scan fails closed above 20,000 paths, 64 MiB per file, or 512 MiB total. Journal records are not a backup system and cannot guarantee reversal of effects outside recorded workspace paths. Use OS-level isolation for untrusted processes.
  Previously retained old inodes, if any, remain in the private journal for inspection/recovery and consume storage until separately cleaned up. Cleanup is manual: stop processes that might hold old handles, inspect/archive the manifest and any late-write data, then remove only selected retained entries; closing the journal is not proof that handles are gone, and there is no age-based auto-deletion.
- The deprecated `register_builtin_tools` bridge registers read-only tools only. Mutating handlers must be invoked through `ToolEngine` so they cannot bypass task journaling and shell approval.
- Python plugins execute inside the agent process with that process's file, network, and command permissions. Plugin loading fails closed when import, initialization, or schema collection fails, but this error handling is not code isolation. Review plugin code and install only plugins you trust.
- The skill installer accepts ZIPs only from `github.com`, `codeload.github.com`, or `gitlab.com`; redirects are checked against the same host allowlist before the follow-up request is made. Downloads have total-size, expanded-size, compression-ratio, inactivity-timeout, and absolute-deadline limits. The assembled skill is previewed and must be approved before it is saved.
- Credential-like files (`.env` variants other than templates, common credential stores, and private keys) are excluded from reads, searches, indexing, and automatic context; file writes and patches to those paths are denied at the tool boundary, including Markdown fallback actions. There is no model-selectable override; the user must explicitly review and copy any needed value to a non-sensitive file. Regular files with multiple hard links are also excluded from reads, searches, indexing, automatic context, directory/glob listings, Git status/diff output, and checkpoints; rollback preserves excluded aliases and atomically replaces files it restores.
- Parallel sub-agents share the same workspace and tool engine; they are not separate OS security boundaries. Checkpoints and rollback are recovery aids, not a substitute for backups or isolation.

## ⚙️ Configuration

Settings live in `~/.zxagent/config.json`:

```json
{
  "provider": "deepseek",
  "model": "deepseek-v4-flash",
  "api_key": "...",
  "base_url": "https://api.deepseek.com/v1/chat/completions",
  "theme": "aurora",
  "multi_ai": {}
}
```

Logs are written (JSON structured) to `~/.zxagent/logs/zx_agent.log`.

## 🛠️ Development

```bash
# Run the test suite (needs pytest / pytest-asyncio)
python -m pytest

# Lint check
python -m pyflakes main.py config core execution indexing mcp memory \
    planner plugins providers storage tools ui utils

# Compile check
python -m compileall -q .
```

### Project layout

```
main.py               CLI entry point (commands, wizards, main loop)
core/agent.py         Hardened ReAct agent loop (retry/compaction/rollback)
core/subagent.py      Parallel sub-agent delegation
providers/            Provider adapters + multi-AI router
tools/engine.py       10 native tools + markdown-protocol fallback
plugins/              Skill manager + Python plugin SDK
planner/              Task planning, reflection, repair
memory/  indexing/    Context assembly (metadata, semantic store, symbols)
execution/            Diff engine, checkpoint, rollback, authorized shell runner
ui/console.py         Aurora terminal UI (themes, live activity panel)
```

## 🗺️ Roadmap

- [x] Retry with backoff + context compaction + anti-loop guard
- [x] Auto checkpoint / auto-rollback
- [x] Parallel sub-agents + `delegate_tasks` tool
- [x] Live activity TUI panel
- [x] pytest suite, structured logging, plugin SDK
- [x] Session persistence (`--continue`, `/resume`, `/new`)
- [x] Project detection and `ZX.md`/`AGENTS.md` instructions
- [x] Seven-role contracts and provider routing (v3.2 first implementation)
- [x] Independent reviewer/security-auditor stages with bounded journal diffs
- [ ] OS-level command isolation (Docker/Podman backend; host mode remains privileged)
- [ ] Persisted task graph and crash-safe mid-task resume
- [ ] Real-provider soak tests against DeepSeek/Claude
- [ ] True alternate-screen full TUI (textual)

## 📝 Release notes

Full version history lives in [CHANGELOG.md](CHANGELOG.md). Older planning, test-report, and architecture documents are kept in [docs/archive/](docs/archive/).

<details>
<summary><b>v3.5.1 — reliability controls</b></summary>

- Every ZX Code chat task takes a process-shared POSIX workspace lock at `.zx_checkpoints/workspace.lock`; another ZX Code process targeting the same workspace is rejected until the first stream ends.
- The lock is advisory. Editors, terminals, build tools, and other non-cooperating processes can still modify files concurrently.
- DAG nodes can optionally declare up to five `verification_commands`. They run only through the configured container Tester sandbox, require explicit command authorization, and receive a read-only workspace mount. Host-mode verification is blocked.
- Verification exit codes and bounded stdout/stderr are stored with node checkpoint evidence. A declared plan must pass for node acceptance; a node without a plan is recorded as `not_configured`.
- Selected crash boundaries are exercised by subprocess tests. In-flight mutation recovery remains conservative and may require manual reconciliation; no exactly-once guarantee is made.

</details>

<details>
<summary><b>v3.5.2 — crash consistency</b></summary>

Checkpoint archives are staged in a private same-directory temporary file and become visible as trusted `.zip` snapshots only after archive completion, file `fsync`, atomic rename, and checkpoint-directory `fsync`. Mutation-journal preimage blobs now sync their containing directory after content-addressed rename. These measures improve crash consistency on filesystems that honor POSIX flush semantics; they do not provide exactly-once behavior for external side effects or guarantees against faulty storage hardware.

</details>

<details>
<summary><b>v3.5.3 — resume & recovery safety</b></summary>

Resume eligibility is centralized in `TaskStateStore.assess_recovery()`. A task is offered explicit-confirmation continuation only when its cursor and DAG metadata are valid, the workspace fingerprint still matches, and every task-owned mutation journal is readable and free of unfinished operations, non-completed tool invocations, and rollback skips. Resume acquires the workspace advisory lock before this assessment and retains it through execution startup. This protects against concurrent ZX Code tasks using the same lock protocol; it does not stop external programs that ignore the lock. Ambiguous states require manual reconciliation. No exactly-once guarantee is provided.

</details>

<details>
<summary><b>v3.5.4 — recovery integrity</b></summary>

Mutation journals now use schema v2 with a durable SHA-256 hash chain. Recovery verifies the complete chain and the current manifest digest; missing, legacy, malformed, truncated, or mismatched evidence blocks continuation. This is tamper-evident local bookkeeping, not a digital signature or protection against an attacker who can rewrite the entire journal directory.

</details>

<details>
<summary><b>v3.5.5 — crash consistency</b></summary>

Rollback now persists a rollback_in_progress intent before restoring any file. If that intent cannot be written, no restore is attempted. A process killed after restoring files but before persisting the final outcome leaves an unresolved journal and blocks resume. Subprocess crash tests cover journal chain/manifest divergence, rollback interruption, task-record/active-pointer ordering, and checkpoint publication boundaries.

</details>

---

## 📄 License

Released under the [MIT License](LICENSE).

<div align="center">

**ZX Code** — plan, edit, run, verify. All from your terminal.

</div>
