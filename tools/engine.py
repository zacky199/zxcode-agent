import asyncio
import re
from pathlib import Path
from typing import Any, Callable, Dict, List

from execution.diff_engine import DiffEngine
from execution.journal import JournalError, MutationCoordinator, MutationJournal
from tools.builtin import BuiltinTools
from tools.mutation_policy import (
    FILE_MUTATION_DISABLED_MESSAGE,
    FILE_MUTATION_TOOLS,
    NATIVE_FILE_MUTATIONS_DEFAULT,
)
from utils import setup_logger

log = setup_logger("zx.tools")


class ToolEngine:
    """
    Central execution engine for the agent.

    Two execution modes are supported:

    1. **Native tool calling** (used with models that support function calls).
       ``get_tool_schemas()`` returns OpenAI-style schemas that are passed to
       the provider; ``execute_tool(name, args)`` runs the matching primitive.

    2. **Markdown protocol fallback** (``execute_markdown_actions``) which
       parses ````` ```file````, ````` ```patch```` and ````` ```bash````
       blocks out of free-form model output. This keeps the agent usable with
       routers/models that do not support tool calling.
    """

    # Commands that always need explicit user confirmation.
    DANGEROUS_PATTERNS = [
        r"rm\s+-rf", r"rm\s+-r", r"rm\s+", r"sudo\s+",
        r"git\s+push", r"git\s+reset\s+--hard", r"git\s+clean",
        r"drop\s+table", r"drop\s+database", r"truncate\s+table",
        r"npm\s+publish", r"pip\s+uninstall", r"apt(?:-get)?\s+(?:purge|remove)",
        r"mkfs\.", r"fdisk", r"dd\s+if=", r":(){", r">\s*/dev/sd", r"kill\s+-9\s+-",
        r"chmod\s+-R\s+777", r"chown\s+-R", r":wq!\s*$", r"python[23]?\s+-m\s+http",
    ]
    JOURNALED_TOOLS = {"write_file", "edit_file", "run_command"}

    def __init__(self, workspace: Path, confirm_callback: Callable[[str], bool] = None,
                 allow_file_mutations: bool = NATIVE_FILE_MUTATIONS_DEFAULT,
                 sandbox_mode: str = "container", allow_host_execution: bool = False,
                 sandbox_runtime: str = None, sandbox_image: str = "zxcode-sandbox:latest",
                 sandbox_network: str = "none"):
        self.workspace = workspace
        self.diff_engine = DiffEngine(workspace)
        self.builtin = BuiltinTools(workspace, command_authorizer=confirm_callback,
            allow_file_mutations=allow_file_mutations, sandbox_mode=sandbox_mode,
            allow_host_execution=allow_host_execution, sandbox_runtime=sandbox_runtime,
            sandbox_image=sandbox_image)
        self.mutation_coordinator = MutationCoordinator()
        # Optional async-safe confirmation hook injected by the UI layer.
        self.confirm_callback = confirm_callback
        self._registry: Dict[str, Callable[..., str]] = self._register_primitives()
        self._plugin_tools: Dict[str, Callable[[Dict[str, Any]], str]] = {}
        self._plugin_schemas: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Tool schema registration
    # ------------------------------------------------------------------
    def _register_primitives(self) -> Dict[str, Callable[..., str]]:
        return {
            "read_file": self.builtin.read_file,
            "list_directory": self.builtin.list_directory,
            "write_file": self.builtin.write_file,
            "edit_file": self.builtin.edit_file,
            "run_command": self.builtin.execute_command,
            "search_files": self.builtin.search_files,
            "glob_files": self.builtin.glob_files,
            "git_status": self.builtin.git_status,
            "git_diff": self.builtin.git_diff,
        }

    # ------------------------------------------------------------------
    # File-mutation opt-in (user-controlled; see tools/mutation_policy.py)
    # ------------------------------------------------------------------
    @property
    def file_mutations_enabled(self) -> bool:
        return bool(self.builtin.allow_file_mutations)

    def set_file_mutations_enabled(self, enabled: bool) -> None:
        """Turn native write_file/edit_file on or off for this engine.

        Called only by trusted code (CLI flag, ``/edits``). It is deliberately
        not exposed as a tool and is never driven by model arguments.
        """
        self.builtin.allow_file_mutations = bool(enabled)

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """OpenAI-compatible function schemas for every tool currently available.

        ``write_file`` / ``edit_file`` are only advertised while file edits are
        enabled, so the model is never offered a tool that will be refused.
        """
        schemas = self._all_tool_schemas()
        if not self.file_mutations_enabled:
            schemas = [s for s in schemas
                       if s.get("function", {}).get("name") not in FILE_MUTATION_TOOLS]
        return schemas

    def _all_tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a non-sensitive text file inside the workspace. Credential-like paths and regular files with multiple hard links are denied. Files over 2000 lines are cut off: pass start_line/end_line to read any part of them.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string", "description": "Path relative to the workspace root."},
                            "start_line": {"type": "integer", "description": "Optional 1-based first line to read."},
                            "end_line": {"type": "integer", "description": "Optional last line to read (inclusive); at most 2000 lines per call."},
                        },
                        "required": ["file_path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_directory",
                    "description": "List safe workspace entries; credential-like paths and hard-linked regular files are omitted.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Directory path relative to workspace root."},
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Create or overwrite a non-sensitive file inside the workspace. Credential-like paths such as environment files, credential stores, and private keys are denied.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string", "description": "Target path relative to workspace root."},
                            "content": {"type": "string", "description": "Full file content to write."},
                        },
                        "required": ["file_path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_file",
                    "description": "Apply a precise SEARCH/REPLACE patch to an existing non-sensitive file. Credential-like paths are denied; search must match an exact unique block.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {"type": "string", "description": "File path relative to workspace root."},
                            "search": {"type": "string", "description": "Exact text block to find."},
                            "replace": {"type": "string", "description": "Text to replace the search block with."},
                        },
                        "required": ["file_path", "search", "replace"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_command",
                    "description": "Run a shell command in the workspace directory. "
                                   "Returns stdout/stderr and the exit code.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string", "description": "Shell command to execute."},
                            "timeout": {"type": "integer", "description": "Optional timeout in seconds (default 60, maximum 900). Raise it for slow builds or test suites."},
                        },
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_files",
                    "description": "Grep non-sensitive files in the workspace. Credential-like paths and regular files with multiple hard links are skipped.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string", "description": "Regex pattern to search for."},
                            "path": {"type": "string", "description": "Subdirectory or file to restrict the search."},
                        },
                        "required": ["pattern"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "glob_files",
                    "description": "Find safe files by a glob pattern (e.g. **/*.py); credential-like paths and hard-linked regular files are omitted.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string", "description": "Glob pattern to match."},
                        },
                        "required": ["pattern"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "git_status",
                    "description": "Show working tree status for safe paths; credential-like and hard-linked regular files are omitted.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "git_diff",
                    "description": "Show unstaged diffs for safe paths; credential-like and hard-linked regular files are omitted.",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delegate_tasks",
                    "description": "Run several independent sub-tasks in parallel as separate agent loops. "
                                   "Each sub-agent explores and works in the workspace on its own small task. "
                                   "Returns a structured summary per sub-agent. Use for parallelizable work.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "tasks": {
                                "type": "array",
                                "description": "List of {name, task} objects to delegate.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "task": {"type": "string"},
                                    },
                                    "required": ["name", "task"],
                                },
                            },
                        },
                        "required": ["tasks"],
                    },
                },
            },
        ] + list(self._plugin_schemas)

    # ------------------------------------------------------------------
    # Plugin extension point
    # ------------------------------------------------------------------
    def register_plugin_tool(self, name: str, handler: Callable) -> None:
        """Register a plugin-provided tool handler."""
        if not isinstance(name, str) or not name or not callable(handler):
            raise ValueError("Plugin tool name and handler must be valid")
        if name in self._registry or name in self._plugin_tools:
            raise ValueError(f"Tool name is already registered: {name}")
        self._plugin_tools[name] = handler
        # A plugin tool is registered but not added to the native schema list
        # until the plugin also returns a schema via get_tools().

    def unregister_plugin_tool(self, name: str) -> None:
        self._plugin_tools.pop(name, None)

    def clear_plugin_tools(self) -> None:
        self._plugin_tools.clear()
        self._plugin_schemas.clear()

    # ------------------------------------------------------------------
    # Native tool execution
    # ------------------------------------------------------------------
    @classmethod
    def is_command_dangerous(cls, command: str) -> bool:
        cmd_lower = command.strip().lower()
        for pattern in cls.DANGEROUS_PATTERNS:
            if re.search(pattern, cmd_lower):
                return True
        return False

    @staticmethod
    def _is_error_output(text: str) -> bool:
        """Heuristic: tool primitives signal failure via 'Error:' prefix or non-zero exit."""
        if not text:
            return False
        stripped = str(text).strip()
        if not stripped:
            return False
        first_line = stripped.splitlines()[0]
        if first_line.startswith("Error:") or first_line.startswith("ERROR:"):
            return True
        # run_command appends "(exit code N)" for non-zero exits.
        for line in stripped.splitlines():
            if line.strip().startswith("(exit code "):
                code = line.strip()[len("(exit code "):-1].strip()
                if code.isdigit() and int(code) != 0:
                    return True
        return False

    def create_mutation_journal(self, owner_id: str = None,
                                checkpoint_enabled: bool = True) -> MutationJournal:
        return MutationJournal(
            self.workspace, owner_id=owner_id,
            coordinator=self.mutation_coordinator,
            checkpoint_enabled=checkpoint_enabled,
        )

    async def _invoke_handler(self, name: str, args: Dict[str, Any], handler,
                              precommit_check=None, retain_displaced=None,
                              parent_anchor=None) -> Dict[str, Any]:
        try:
            if name in ("write_file", "edit_file") and not self.file_mutations_enabled:
                return {"tool": name, "status": "error",
                        "output": FILE_MUTATION_DISABLED_MESSAGE}
            if name == "run_command":
                if args.get("timeout") is not None:
                    result = await asyncio.to_thread(
                        handler, str(args.get("command", "")), timeout=args.get("timeout"))
                else:
                    result = await asyncio.to_thread(handler, str(args.get("command", "")))
                return self._wrap_result(name, result)
            if name == "write_file":
                if (precommit_check is not None
                        and getattr(handler, "__self__", None) is self.builtin):
                    result = await asyncio.to_thread(
                        handler, args.get("file_path", ""), args.get("content", ""),
                        before_replace=precommit_check,
                        retain_displaced=retain_displaced,
                        parent_anchor=parent_anchor)
                else:
                    result = await asyncio.to_thread(handler, **args)
                return self._wrap_result(name, result)
            if name == "edit_file":
                if (precommit_check is not None
                        and getattr(handler, "__self__", None) is self.builtin):
                    result = await asyncio.to_thread(
                        handler, args.get("file_path", ""),
                        args.get("search", ""), args.get("replace", ""),
                        before_replace=precommit_check,
                        retain_displaced=retain_displaced,
                        parent_anchor=parent_anchor)
                else:
                    result = await asyncio.to_thread(
                        handler, args.get("file_path", ""),
                        args.get("search", ""), args.get("replace", ""))
                return self._wrap_result(name, result)
            if name in ("read_file", "list_directory", "git_status", "git_diff"):
                result = await asyncio.to_thread(handler, **args) if args else handler()
                return self._wrap_result(name, result)
            result = await asyncio.to_thread(handler, **args)
            return self._wrap_result(name, result)
        except TypeError as exc:
            log.warning("tool invalid args", extra={"fields": {"tool": name, "error": str(exc)}})
            return {"tool": name, "status": "error", "output": f"Invalid arguments: {exc}"}
        except Exception as exc:
            log.error("tool crashed", extra={"fields": {"tool": name, "error": str(exc)}})
            return {"tool": name, "status": "error", "output": f"{type(exc).__name__}: {exc}"}

    async def execute_tool(self, name: str, args: Dict[str, Any],
                           journal: MutationJournal = None) -> Dict[str, Any]:
        """Execute a tool; built-in mutations require a durable pre-state first."""
        if name in ("write_file", "edit_file") and not self.file_mutations_enabled:
            return {"tool": name, "status": "error",
                    "output": FILE_MUTATION_DISABLED_MESSAGE}
        args = args or {}
        handler = self._registry.get(name)
        if handler is None and name in self._plugin_tools:
            # Plugins remain process-privileged and are outside file-tool journaling.
            try:
                return self._wrap_result(name, self._plugin_tools[name](args))
            except Exception as exc:
                return {"tool": name, "status": "error",
                        "output": f"{type(exc).__name__}: {exc}"}
        if handler is None:
            log.warning("unknown tool requested", extra={"fields": {"tool": name}})
            return {"tool": name, "status": "error", "output": f"Unknown tool: {name}"}

        if name not in self.JOURNALED_TOOLS:
            return await self._invoke_handler(name, args, handler)

        owns_journal = journal is None
        try:
            active_journal = journal or self.create_mutation_journal()
            ticket = await asyncio.to_thread(active_journal.begin_operation, name, args)
        except Exception as exc:
            if owns_journal and "active_journal" in locals():
                try:
                    active_journal.close()
                except Exception:
                    pass
            return {"tool": name, "status": "error",
                    "output": f"Access denied: mutation blocked because task journal/checkpoint failed before execution: {exc}"}

        parent_anchor = None
        try:
            precommit_check = None
            retain_displaced = None
            if name in ("write_file", "edit_file"):
                parent_anchor = await asyncio.to_thread(
                    active_journal.open_parent_anchor, ticket)
                precommit_check = lambda candidate, anchor: active_journal.assert_precommit_owned(
                    ticket, candidate, anchor)
                retain_displaced = lambda candidate, anchor: active_journal.retain_displaced_preimage(
                    ticket, candidate, anchor)
            result = await self._invoke_handler(
                name, args, handler, precommit_check=precommit_check,
                retain_displaced=retain_displaced, parent_anchor=parent_anchor)
            operation = await asyncio.to_thread(active_journal.finish_operation, ticket)
            if operation.capture_error:
                active_journal.commit_operation(operation)
                result["status"] = "error"
                result["output"] = (str(result.get("output", "")) +
                    "\nWARNING: mutation may have run, but its post-state could not be verified; "
                    "files were preserved rather than restored.")
            elif (result.get("status") == "ok" and ticket.scope == "paths"
                  and ticket.paths and ticket.paths[0] in operation.ambiguous_paths):
                result["status"] = "error"
                result["output"] = (str(result.get("output", "")) +
                    "\nJournal conflict/ambiguous: native output or target ownership "
                    "could not be proven; the target was preserved without rollback.")
                report = active_journal.rollback_failed_operation(operation)
                result["output"] += f"\nJournal recovery: {report.describe()}."
            elif result.get("status") == "ok":
                active_journal.commit_operation(operation)
                if operation.ambiguous_paths:
                    paths = ", ".join(sorted(operation.ambiguous_paths)[:8])
                    result["output"] = (str(result.get("output", "")) +
                        "\nJournal conflict/ambiguous provenance: preserved workspace "
                        f"delta(s) without automatic rollback: {paths}.")
            else:
                report = active_journal.rollback_failed_operation(operation)
                result["output"] = (str(result.get("output", "")) +
                                     f"\nJournal recovery: {report.describe()}.")
            return result
        except Exception as exc:
            try:
                active_journal.abort_operation(ticket)
            except Exception:
                pass
            return {"tool": name, "status": "error",
                    "output": f"{type(exc).__name__}: {exc}"}
        finally:
            if parent_anchor is not None:
                try:
                    parent_anchor.close()
                except Exception:
                    pass
                ticket.parent_anchor = None
            if owns_journal:
                try:
                    active_journal.close()
                except Exception:
                    pass

    @staticmethod
    def _wrap_result(name: str, result) -> Dict[str, Any]:
        output = result if isinstance(result, str) else str(result)
        if ToolEngine._is_error_output(output):
            return {"tool": name, "status": "error", "output": output}
        return {"tool": name, "status": "ok", "output": output}

    # ------------------------------------------------------------------
    # Markdown protocol fallback (```` ```file / ```patch / ```bash ````)
    # ------------------------------------------------------------------
    def _extract_bash_commands(self, text: str) -> List[str]:
        pattern = re.compile(r"```(?:bash|sh|shell)\s*\n(.*?)```", re.DOTALL)
        return [m.group(1).strip() for m in pattern.finditer(text) if m.group(1).strip()]

    def _extract_file_blocks(self, text: str) -> List[tuple]:
        pattern = re.compile(r"```file\s+([^\n`]+)\n(.*?)```", re.DOTALL)
        return [(m.group(1).strip(), m.group(2)) for m in pattern.finditer(text)]

    def _extract_patch_blocks(self, text: str) -> List[tuple]:
        pattern = re.compile(
            r"```patch\s+([^\n`]+)\n<<<< SEARCH\n(.*?)====\n(.*?)>>>> REPLACE\n```",
            re.DOTALL,
        )
        return [(m.group(1).strip(), m.group(2), m.group(3)) for m in pattern.finditer(text)]

    async def execute_markdown_actions(
        self,
        accumulated_text: str,
        before_action: Callable[[str], Any] = None,
        after_action: Callable[[str, Any, str], bool] = None,
        journal: MutationJournal = None,
    ) -> str:
        """Run Markdown mutations through the same journal gate as native calls."""
        log: List[str] = []
        owns_journal = journal is None
        active_journal = journal
        stop_actions = False

        async def run_action(name: str, args: Dict[str, Any], label: str) -> None:
            nonlocal stop_actions
            snapshot = None
            if callable(before_action):
                try:
                    snapshot = before_action(name)
                except Exception as exc:
                    result = {"status": "error", "output": f"Checkpoint callback failed: {exc}"}
                else:
                    result = await execute_action(name, args)
            else:
                result = await execute_action(name, args)
            status, output = result.get("status", "error"), result.get("output", "")
            log.append(f"{label} {status}: {output}")
            if callable(after_action):
                try:
                    if after_action(name, snapshot, status) is False:
                        stop_actions = True
                except Exception:
                    stop_actions = True

        async def execute_action(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
            nonlocal active_journal
            if name in ("write_file", "edit_file"):
                # The public ToolEngine gate rejects these before journal setup;
                # do not create checkpoint files merely to report the denial.
                return await self.execute_tool(name, args, journal=active_journal)
            if active_journal is None:
                try:
                    active_journal = self.create_mutation_journal()
                except Exception as exc:
                    return {"tool": name, "status": "error",
                            "output": f"Access denied: mutation blocked because task journal/checkpoint failed before execution: {exc}"}
            return await self.execute_tool(name, args, journal=active_journal)

        try:
            for filepath, content in self._extract_file_blocks(accumulated_text):
                await run_action("write_file", {
                    "file_path": filepath, "content": content.rstrip("\n")},
                    f"[FILE: {filepath}]")
                if stop_actions:
                    break
            if not stop_actions:
                for filepath, search, replace in self._extract_patch_blocks(accumulated_text):
                    await run_action("edit_file", {
                        "file_path": filepath, "search": search, "replace": replace},
                        f"[PATCH: {filepath}]")
                    if stop_actions:
                        break
            if not stop_actions:
                for command in self._extract_bash_commands(accumulated_text):
                    await run_action("run_command", {"command": command},
                                     f"[BASH] $ {command}")
                    if stop_actions:
                        break
        finally:
            if owns_journal and active_journal is not None:
                summary = active_journal.close()
                if summary:
                    log.append(summary)
        return "\n".join(log) if log else ""
