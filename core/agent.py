"""
ZX Agent core — robust ReAct agent loop.

The loop adds the following professional-grade safeguards on top of the
basic ReAct cycle:

* **Retry with exponential backoff** on transient provider/network errors
  (timeouts, 429/5xx, connection resets).
* **Context compaction**: when the message buffer exceeds the token budget,
  older messages are rolled up into a deterministic summary instead of
  silently overflowing the model context window.
* **Anti-infinite-loop guard**: identical repeated tool calls and repeated
  consecutive failures abort the run instead of spinning forever.
* **Auto-checkpoint**: a ZIP snapshot is taken before the first mutating
  tool call of a run, and can be restored automatically if the run fails
  catastrophically.
* **Memory + indexing integration**: tool outcomes are persisted to the
  semantic store and the repository symbol graph is injected into the
  prompt so the model never works blind.
"""

import asyncio
import json
import random
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from core.lifecycle import AgentLifecycle, AgentState
from planner import TaskPlanner, TaskRepairer
from planner.triage import should_plan
from memory import MemoryManager
from memory.instructions import load_project_instructions
from memory.session_store import SessionStore
from indexing import SemanticIndexer, FileWatcher
from tools import ToolEngine
from execution.checkpoint import CheckpointManager
from execution.journal import MutationJournal
from execution.rollback import RollbackManager
from execution.task_state import TaskStateStore
from execution.acceptance import NodeAcceptanceContract
from execution.workspace_lock import WorkspaceLock, WorkspaceBusyError
from execution.verification import NodeVerificationPlan
from execution.resume import ExecutionCursor
from core.roles import parse_review_findings, parse_test_commands
from execution.test_runner import TesterRunner
from utils.filesystem import FileSystemUtils
from utils.sensitive_files import is_sensitive_path
from core.subagent import SubAgentManager
from core.roles import RoleOrchestrator
from utils import setup_logger
from utils.text import truncate_middle

log = setup_logger("zx.agent")

SYSTEM_PROMPT = """You are ZX Agent Pro, a professional autonomous AI software engineer.

CORE PRINCIPLES:
1. You work inside a workspace and make real changes: create files, edit code, run commands.
2. Always explore the workspace before assuming anything about the project.
3. Use the available tools to accomplish the user's goal. Verify your work (run tests/builds).
4. Be concise in your commentary. Output your reasoning briefly, then act.
5. When a step fails, read the error, fix the root cause, and retry with a different approach.
6. Never claim success you cannot verify. If you cannot verify, say so explicitly.

TOOL USAGE:
- Use read_file / list_directory / search_files / glob_files to inspect the project.
  read_file returns at most 2000 lines per call: pass start_line/end_line to read any part of a
  large file. When you need several independent reads or searches, request them together in one
  turn; read-only calls run in parallel.
{file_edit_guidance}
- Use run_command to execute builds, tests, package installs or one-off shell commands. Pass
  timeout (seconds, up to 900) for slow builds or test suites. Very long output is shortened to
  its head and tail, so prefer commands that print only what matters.
- Use git_status / git_diff to understand repository state.

WORKING STYLE:
- Make minimal, precise edits. Preserve surrounding code and comments.
- For frontend tasks, run the dev server and inspect output; fix errors before finishing.
- Prefer standard tooling (npm/pip/go) appropriate to the project.
- If a command needs installation, install it first, then run it.
- After changing code, run the project's own checks. The CONTEXT lists "Suggested verify
  commands" when they could be detected; if the project ships instructions, follow them.
"""

FILE_EDIT_GUIDANCE_ENABLED = (
    "- Use write_file to create files and edit_file (SEARCH/REPLACE) to modify existing code.\n"
    "  write_file never creates missing directories: create a new folder first with\n"
    "  run_command (mkdir -p <dir>), then write files into it."
)
FILE_EDIT_GUIDANCE_DISABLED = (
    "- File edit tools (write_file / edit_file) are NOT available in this session; do not call them.\n"
    "  To create or change a file, use run_command (the user reviews and approves each command),\n"
    "  or tell the user they can enable native edits by restarting with --allow-edits or running /edits on."
)


def build_system_prompt(file_edits_enabled: bool) -> str:
    """Return the system prompt matching the currently available file tools."""
    guidance = FILE_EDIT_GUIDANCE_ENABLED if file_edits_enabled else FILE_EDIT_GUIDANCE_DISABLED
    return SYSTEM_PROMPT.replace("{file_edit_guidance}", guidance)


# Tool calls that mutate the workspace (trigger checkpointing).
MUTATING_TOOLS = {"write_file", "edit_file", "run_command", "apply_patch", "delegate_tasks"}
# Tools that only read state; several of them in one model turn run concurrently.
READ_ONLY_TOOLS = {"read_file", "list_directory", "search_files", "glob_files",
                   "git_status", "git_diff"}
SUMMARY_PREFIX = "[Prior context summarized]"
PLANNING_MODES = ("auto", "always", "never")


class AgentConfig:
    """Tunable parameters for the ReAct loop."""

    def __init__(self,
                 max_iterations: int = 30,
                 max_context_tokens: int = 110_000,
                 compaction_threshold: float = 0.7,
                 max_retries: int = 3,
                 retry_base_delay: float = 1.0,
                 retry_max_delay: float = 12.0,
                 max_repeated_tool_calls: int = 3,
                 max_consecutive_failures: int = 4,
                 checkpoint_mutations: bool = True,
                 auto_rollback: bool = True,
                 store_tool_memory: bool = True,
                 planning: str = "auto",
                 max_tool_output_chars: int = 12_000,
                 sandbox_mode: str = "container",
                 allow_host_execution: bool = False,
                 sandbox_runtime: str = None,
                 sandbox_image: str = "zxcode-sandbox:latest",
                 sandbox_network: str = "none"):
        self.max_iterations = max_iterations
        # "auto" skips the planner for greetings and short questions,
        # "always" plans every request, "never" never plans.
        self.planning = planning if planning in PLANNING_MODES else "auto"
        # Longest tool output kept in the model context (head + tail are kept).
        self.max_tool_output_chars = max(500, int(max_tool_output_chars))
        self.max_context_tokens = max_context_tokens
        self.compaction_threshold = compaction_threshold
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.retry_max_delay = retry_max_delay
        self.max_repeated_tool_calls = max_repeated_tool_calls
        self.max_consecutive_failures = max_consecutive_failures
        self.checkpoint_mutations = checkpoint_mutations
        self.auto_rollback = auto_rollback
        self.store_tool_memory = store_tool_memory
        self.sandbox_mode = sandbox_mode
        self.allow_host_execution = bool(allow_host_execution)
        self.sandbox_runtime = sandbox_runtime
        self.sandbox_image = sandbox_image
        self.sandbox_network = sandbox_network


class ZXAgentPro:
    """Autonomous coding agent with a resilient ReAct loop."""

    def __init__(self, provider: Any, workspace: Path, skill_content: str = "",
                 confirm_callback: Callable[[str], bool] = None,
                 role_router: Any = None,
                 config: Optional[AgentConfig] = None,
                 plugin_approval_callback: Callable[[str], bool] = None,
                 allow_file_edits: bool = False,
                 session_store: Optional[SessionStore] = None):
        self.provider = provider
        self.session_store = session_store
        self.workspace = workspace
        self.skill_content = skill_content
        self.role_router = role_router
        self.roles = RoleOrchestrator(provider, role_router)
        self.config = config or AgentConfig()
        self.plugin_approval_callback = plugin_approval_callback

        self.lifecycle = AgentLifecycle()
        model_name = getattr(provider, "model", "gpt-4o")
        self.memory = MemoryManager(workspace, model_name)
        self.indexer = SemanticIndexer(workspace)
        self.watcher = FileWatcher(workspace)
        self.planner = TaskPlanner(llm_callback=self._planner_complete)
        self.repairer = TaskRepairer(llm_callback=self._llm_completion_shim)
        self.tool_engine = ToolEngine(workspace, confirm_callback=confirm_callback,
            allow_file_mutations=allow_file_edits, sandbox_mode=self.config.sandbox_mode,
            allow_host_execution=self.config.allow_host_execution,
            sandbox_runtime=self.config.sandbox_runtime, sandbox_image=self.config.sandbox_image,
            sandbox_network=self.config.sandbox_network)
        self.checkpoints = CheckpointManager(workspace)
        self.task_state = TaskStateStore(workspace)
        self.rollback = RollbackManager(
            workspace, trusted_snapshots=self.checkpoints.trusted_snapshots)
        self.subagents = SubAgentManager(
            workspace, self.tool_engine, provider, confirm_callback=confirm_callback,
            checkpoint_enabled=self.config.checkpoint_mutations)
        self.indexer.scan_workspace()

        # Plugin registry: loads third-party Python plugins and merges their
        # tool schemas + handlers into the tool engine.
        self.plugins = self._load_plugins(workspace)

        self._tokenizer = None
        try:
            from utils.tokenizer import TokenizerUtils
            self._tokenizer = TokenizerUtils(model_name)
        except Exception:
            self._tokenizer = None

    # ------------------------------------------------------------------
    # LLM helpers
    # ------------------------------------------------------------------
    async def _llm_completion_shim(self, system: str, prompt: str) -> str:
        provider = self.roles.provider_for("debugger")
        response = await self._complete_with_retry(
            provider, system, [{"role": "user", "content": prompt}]
        )
        return response.text

    def _planner_provider(self):
        return self.roles.provider_for("planner")

    async def _planner_complete(self, system: str, prompt: str) -> str:
        provider = self._planner_provider()
        response = await self._complete_with_retry(
            provider, system, [{"role": "user", "content": prompt}]
        )
        return response.text

    # ------------------------------------------------------------------
    # Plugin loading
    # ------------------------------------------------------------------
    def _load_plugins(self, workspace: Path):
        from plugins import PluginRegistry, PluginLoader, PluginContext
        from config.constants import DEFAULT_PLUGIN_DIR

        registry = PluginRegistry()
        ctx = PluginContext(workspace=workspace, tool_engine=self.tool_engine,
                            memory=self.memory, logger=log)
        loader = PluginLoader(
            registry, ctx=ctx, approval_callback=self.plugin_approval_callback)

        try:
            # Workspace contents may be untrusted; do not execute project-local Python plugins.
            loader.load_from_directory(DEFAULT_PLUGIN_DIR)
            schemas = registry.collect_tool_schemas()
            for schema in schemas:
                fname = schema["function"]["name"]
                if fname not in self.tool_engine._plugin_tools:
                    for plugin in registry.all():
                        if any(item.get("function", {}).get("name") == fname
                               for item in (plugin.get_tools() or [])):
                            handler = getattr(plugin, "execute_tool", None)
                            if handler is not None:
                                self.tool_engine.register_plugin_tool(
                                    fname, lambda args, h=handler, f=fname: h(f, args))
                            break
                self.tool_engine._plugin_schemas.append(schema)
        except Exception as exc:
            log.error("plugin loading failed closed; disabling all plugins: %s", exc)
            registry.shutdown_all()
            self.tool_engine.clear_plugin_tools()
        return registry

    # ------------------------------------------------------------------
    # Retry helpers (exponential backoff with jitter)
    # ------------------------------------------------------------------
    @staticmethod
    def _is_retryable_error(message: str) -> bool:
        lowered = (message or "").lower()
        return any(token in lowered for token in (
            "timeout", "timed out", "connection reset", "connection refused",
            "network", "429", "503", "502", "500", "rate limit", "overloaded",
            "temporarily unavailable", "econnreset", "econnrefused",
            "remote protocol error",
        ))

    async def _complete_with_retry(self, provider, system: str, messages: list,
                                   tools: list = None) -> Any:
        delay = self.config.retry_base_delay
        for attempt in range(self.config.max_retries + 1):
            try:
                return await provider.complete_async(system, messages, tools=tools)
            except Exception as e:
                if not self._is_retryable_error(str(e)) or attempt >= self.config.max_retries:
                    raise
                await asyncio.sleep(delay + random.uniform(0, 0.5))
                delay = min(delay * 2, self.config.retry_max_delay)
        raise RuntimeError("Unreachable")

    async def _stream_with_retry(self, provider, system: str, messages: list,
                                 tools: list = None) -> AsyncGenerator[Dict[str, Any], None]:
        """Yield provider events, transparently retrying transient failures.

        Retries only when the stream has produced no meaningful content yet
        (nothing was yielded), so the model's partial output is never lost.
        """
        delay = self.config.retry_base_delay
        attempt = 0
        while True:
            yielded_content = False
            produced: Dict[str, Any] = None
            try:
                async for chunk in provider.stream_complete_async(system, messages, tools=tools):
                    ctype = chunk.get("type")
                    if ctype in ("text", "tool_call"):
                        yielded_content = True
                    if ctype == "error":
                        produced = chunk
                        continue
                    yield chunk
            except Exception as e:
                message = str(e)
                if not self._is_retryable_error(message) or attempt >= self.config.max_retries:
                    if produced is not None:
                        yield produced
                    else:
                        yield {"type": "error", "content": message}
                    return
            else:
                # Stream ended without an exception. Propagate any captured
                # error event, or retry if it was transient and nothing useful
                # was produced yet.
                if produced is not None:
                    if (self._is_retryable_error(str(produced.get("content", "")))
                            and not yielded_content
                            and attempt < self.config.max_retries):
                        attempt += 1
                        yield {"type": "status",
                               "content": f"Transient error, retrying in {delay:.1f}s "
                                          f"(attempt {attempt}/{self.config.max_retries})"}
                        await asyncio.sleep(delay + random.uniform(0, 0.5))
                        delay = min(delay * 2, self.config.retry_max_delay)
                        continue
                    yield produced
                return

            # A transient exception happened before any real content.
            if yielded_content:
                yield {"type": "error", "content": "Stream failed after partial output."}
                return

            attempt += 1
            yield {"type": "status",
                   "content": f"Transient error, retrying in {delay:.1f}s (attempt {attempt}/{self.config.max_retries})"}
            await asyncio.sleep(delay + random.uniform(0, 0.5))
            delay = min(delay * 2, self.config.retry_max_delay)

    # ------------------------------------------------------------------
    # Context assembly (index + memory aware)
    # ------------------------------------------------------------------
    def _get_workspace_snapshot(self, depth: int = 0, path: Path = None) -> str:
        workspace_root = Path(self.workspace).resolve()
        path = Path(path or self.workspace)
        snapshot = []
        try:
            if is_sensitive_path(path) or path.is_symlink():
                return ""
            path.resolve().relative_to(workspace_root)
            entries = sorted(
                path.iterdir(),
                key=lambda item: (item.is_symlink() or not item.is_file(), item.name.lower()),
            )
            for item in entries:
                if item.name.startswith(".") or item.name in ("node_modules",):
                    continue
                if is_sensitive_path(item) or item.is_symlink():
                    continue
                try:
                    safe_item = item.resolve()
                    safe_item.relative_to(workspace_root)
                except (OSError, ValueError):
                    continue
                if is_sensitive_path(safe_item):
                    continue
                if FileSystemUtils.is_hard_linked_regular_file(safe_item):
                    continue
                if safe_item.is_dir():
                    snapshot.append(f"- {item.name}/")
                    if depth < 2:
                        snapshot.extend(self._get_workspace_snapshot(depth + 1, safe_item))
                else:
                    size = safe_item.stat().st_size
                    snapshot.append(f"- {item.name} ({size}B)")
                    if size < 4000 and item.suffix in (".py", ".js", ".ts", ".jsx", ".tsx",
                                                       ".json", ".md", ".html", ".css", ".sh"):
                        try:
                            content = safe_item.read_text(encoding="utf-8", errors="ignore")[:2500]
                            snapshot.append(f"    <contents>{content}</contents>")
                        except Exception:
                            pass
        except Exception as e:
            snapshot.append(f"- (error reading dir: {e})")
        return "\n".join(snapshot)

    def _get_symbol_overview(self) -> str:
        """Compact symbol index (class/function names per file) from tree-sitter."""
        try:
            data = self.indexer.indexed_data
            if not data:
                return ""
            lines = []
            for item in data:
                indexed_path = self.workspace / item.get("path", "")
                if (indexed_path.is_symlink() or is_sensitive_path(indexed_path)
                        or FileSystemUtils.is_hard_linked_regular_file(indexed_path)):
                    continue
                syms = [f"{s['type'][0]}:{s['name']}" for s in item.get("symbols", [])][:20]
                if syms:
                    lines.append(f"- {item['path']} :: {', '.join(syms)}")
            if not lines:
                return ""
            return "[REPOSITORY SYMBOLS]\n" + "\n".join(lines[:60])
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Session persistence
    # ------------------------------------------------------------------
    def save_session(self) -> bool:
        """Persist the conversation for this workspace (never raises)."""
        if self.session_store is None:
            return False
        try:
            return self.session_store.save(self.workspace, self.memory.get_chat_history())
        except Exception:
            return False

    def restore_session(self) -> int:
        """Load the saved conversation for this workspace; returns messages restored."""
        if self.session_store is None:
            return 0
        messages = self.session_store.load(self.workspace)
        if messages:
            self.memory.conversation.history = messages
        return len(messages)

    def reset_conversation(self) -> None:
        """Forget the conversation (and its saved copy) and start fresh."""
        self.memory.conversation.history = []
        if self.session_store is not None:
            self.session_store.clear(self.workspace)

    def _build_context(self, user_prompt: str) -> str:
        snapshot = self._get_workspace_snapshot()
        base_context = self.memory.build_context_for_query(user_prompt)
        symbols = self._get_symbol_overview()
        skill_block = f"\n# ACTIVE SKILL\n{self.skill_content}" if self.skill_content else ""
        instructions = load_project_instructions(self.workspace)
        instructions_block = f"\n\n{instructions}" if instructions else ""
        return (f"### WORKSPACE SNAPSHOT ###\n{snapshot}\n\n{base_context}"
                f"{instructions_block}\n\n{symbols}{skill_block}")

    # ------------------------------------------------------------------
    # Token accounting / compaction
    # ------------------------------------------------------------------
    def _count_tokens(self, text: str) -> int:
        if self._tokenizer is not None:
            try:
                return self._tokenizer.count_tokens(text or "")
            except Exception:
                pass
        return len(text or "") // 4

    def _messages_tokens(self, messages: List[Dict[str, Any]]) -> int:
        return sum(
            self._count_tokens(str(msg.get("content") or ""))
            + sum(self._count_tokens(str(
                      tc.get("arguments") or
                      (tc.get("function") or {}).get("arguments") or ""))
                  for tc in msg.get("tool_calls") or [])
            for msg in messages
        )

    def _compact_messages(self, messages: List[Dict[str, Any]],
                          system_prompt: str) -> List[Dict[str, Any]]:
        """Roll older messages into a summary when the context is near full."""
        limit = self.config.max_context_tokens
        if self._messages_tokens(messages) <= int(limit * self.config.compaction_threshold):
            return messages

        prior_summaries = [
            m for m in messages
            if m.get("role") == "system"
            and str(m.get("content") or "").startswith(SUMMARY_PREFIX)]
        system_messages = [
            m for m in messages
            if m.get("role") == "system" and not any(m is old for old in prior_summaries)]
        conversation = [m for m in messages if m.get("role") != "system"]
        prelude: List[Dict[str, Any]] = []
        turns: List[List[Dict[str, Any]]] = []
        current_turn: List[Dict[str, Any]] = []
        for message in conversation:
            if message.get("role") == "user":
                if current_turn:
                    turns.append(current_turn)
                current_turn = [message]
            elif current_turn:
                current_turn.append(message)
            else:
                prelude.append(message)
        if current_turn:
            turns.append(current_turn)
        if not turns:
            return messages

        def complete_tool_turn(turn: List[Dict[str, Any]]) -> bool:
            pending: Optional[set] = None
            for message in turn[1:]:
                role = message.get("role")
                if role == "assistant":
                    if pending:
                        return False
                    calls = message.get("tool_calls") or []
                    if calls:
                        call_ids = [call.get("id") for call in calls]
                        if (any(not call_id for call_id in call_ids)
                                or len(set(call_ids)) != len(call_ids)):
                            return False
                        pending = set(call_ids)
                elif role == "tool":
                    call_id = message.get("tool_call_id")
                    if pending is None or call_id not in pending:
                        return False
                    pending.remove(call_id)
                    if not pending:
                        pending = None
                elif pending:
                    return False
            return pending is None

        # Keep whole user-delimited turns, expanding beyond six raw messages
        # when needed to retain the newest request and its complete tool cycle.
        recent_turns: List[List[Dict[str, Any]]] = []
        retained_message_count = 0
        for turn in reversed(turns):
            if recent_turns and retained_message_count + len(turn) > 6:
                break
            recent_turns.append(turn)
            retained_message_count += len(turn)
        recent_turns.reverse()

        retained_turns: List[List[Dict[str, Any]]] = []
        for index, turn in enumerate(recent_turns):
            if complete_tool_turn(turn):
                retained_turns.append(turn)
            elif index == len(recent_turns) - 1:
                # Always preserve the newest user request, but never send an
                # orphaned or incomplete tool-call sequence to the provider.
                retained_turns.append([turn[0]])

        retained_ids = {id(message) for turn in retained_turns for message in turn}
        omitted = prior_summaries + prelude + [
            message for turn in turns for message in turn
            if id(message) not in retained_ids
        ]
        summary_text = self._summarize_omitted(omitted)
        compacted = list(system_messages)
        if omitted:
            compacted.append({"role": "system", "content": summary_text})
        compacted.extend(message for turn in retained_turns for message in turn)
        return compacted

    _SUMMARY_KEY_ARGS = ("file_path", "path", "command", "pattern", "query", "glob")

    @classmethod
    def _summarize_omitted(cls, omitted: List[Dict[str, Any]], max_chars: int = 3000) -> str:
        """Deterministic digest of dropped messages: requests, tool use, and failures."""
        lines: List[str] = []
        for message in omitted:
            role = message.get("role")
            content = " ".join(str(message.get("content") or "").split())
            if role == "system" and content.startswith(SUMMARY_PREFIX):
                body = content[len(SUMMARY_PREFIX):].strip()
                if body:
                    lines.append(body)
            elif role == "user" and content:
                lines.append("user: " + content[:200])
            elif role == "assistant":
                if content:
                    lines.append("assistant: " + content[:200])
                for call in message.get("tool_calls") or []:
                    function = call.get("function") or {}
                    name = function.get("name") or call.get("name") or "tool"
                    raw_args = function.get("arguments") or call.get("arguments") or {}
                    if isinstance(raw_args, str):
                        try:
                            raw_args = json.loads(raw_args)
                        except ValueError:
                            raw_args = {}
                    detail = ""
                    if isinstance(raw_args, dict):
                        for key in cls._SUMMARY_KEY_ARGS:
                            if raw_args.get(key):
                                detail = str(raw_args[key])
                                break
                    lines.append(f"  - {name}({' '.join(detail.split())[:100]})")
            elif role == "tool" and content:
                lowered = content.lower()
                if (lowered.startswith(("error", "access denied", "timeout"))
                        or "(exit code" in lowered):
                    lines.append("  ! failed: " + content[:160])
        body = truncate_middle("\n".join(lines), max_chars, head_ratio=0.35)
        return f"{SUMMARY_PREFIX} " + body

    # ------------------------------------------------------------------
    # Checkpoint / rollback
    # ------------------------------------------------------------------
    def _maybe_checkpoint(self, tool_name: str) -> Optional[Path]:
        if not self.config.checkpoint_mutations or tool_name not in MUTATING_TOOLS:
            return None
        try:
            return self.checkpoints.create_snapshot("pre_mutation")
        except Exception:
            return None

    async def _auto_rollback(self, snapshot: Optional[Path] = None) -> bool:
        if not self.config.auto_rollback or snapshot is None:
            return False
        try:
            self.rollback.restore_snapshot(snapshot)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Streaming event protocol:
    #   {"type": "status"|"plan"|"text"|"tool_call"|"tool_result"|"done"|"error", "content": ...}
    # ------------------------------------------------------------------
    async def resume_task_stream(self, task_id: str) -> AsyncGenerator[Dict[str, Any], None]:
        """Validate and resume while holding the workspace lock across the TOCTOU window."""
        lock = WorkspaceLock(self.workspace)
        try:
            lock.acquire()
        except WorkspaceBusyError as exc:
            yield {"type": "error", "content": str(exc)}
            return
        except Exception as exc:
            yield {"type": "error", "content": f"Workspace lock unavailable: {type(exc).__name__}: {exc}"}
            return
        try:
            loaded = self.task_state.load(task_id)
            if loaded.get("status") != "stored":
                yield {"type": "error", "content": f"Task state unavailable: {loaded.get('status', 'unknown')}"}
                return
            state = loaded["state"]
            decision = self.task_state.assess_recovery(task_id)
            if decision.get("action") != "restart_from_user_confirmation":
                yield {"type": "error", "content": (
                    "Task cannot be resumed automatically. "
                    + decision.get("reason", decision.get("action", "unsafe state"))
                    + ". Inspect workspace changes and reconcile the interrupted node manually.")}
                return
            try:
                restored_graph = self.planner.from_dict(state.get("payload", {}).get("task_graph", {}))
                if not restored_graph.nodes:
                    raise ValueError("empty graph")
            except Exception as exc:
                yield {"type": "error", "content": f"Stored task graph is not resumable: {exc}. Start a new task instead."}
                return
            goal = state.get("payload", {}).get("goal", "")
            if not goal:
                yield {"type": "error", "content": "Stored task has no recoverable goal."}
                return
            # Do not reacquire through chat_stream: this lock protects the recovery
            # assessment, fingerprint scan, graph load, and resumed execution start.
            async for event in self._chat_stream_unlocked(goal, _resume_state=state):
                yield event
        except Exception as exc:
            log.exception("task resume failed", extra={"fields": {"task_id": task_id}})
            yield {"type": "error", "content": f"Task resume aborted safely: {exc}"}
        finally:
            lock.release()

    def _save_task_checkpoint(self, task_id: str, stage: str, payload: Dict[str, Any],
                              cursor: ExecutionCursor) -> None:
        """Persist task metadata and a bounded execution cursor atomically."""
        checkpoint_payload = dict(payload)
        checkpoint_payload["execution_cursor"] = cursor.to_dict()
        self.task_state.save(task_id, stage, checkpoint_payload)

    async def chat_stream(self, user_prompt: str,
                          _resume_state: Optional[Dict[str, Any]] = None) -> AsyncGenerator[Dict[str, Any], None]:
        """Serialize ZX Code tasks per workspace; OS releases lock on process death."""
        lock = WorkspaceLock(self.workspace)
        try:
            lock.acquire()
        except WorkspaceBusyError as exc:
            yield {"type": "error", "content": str(exc)}
            return
        except Exception as exc:
            yield {"type": "error", "content": f"Workspace lock unavailable: {type(exc).__name__}: {exc}"}
            return
        try:
            async for event in self._chat_stream_unlocked(user_prompt, _resume_state=_resume_state):
                yield event
        finally:
            lock.release()

    async def _chat_stream_unlocked(self, user_prompt: str,
                                    _resume_state: Optional[Dict[str, Any]] = None) -> AsyncGenerator[Dict[str, Any], None]:
        resume_payload = (_resume_state or {}).get("payload", {})
        task_id = (_resume_state or {}).get("task_id") or f"task-{uuid.uuid4().hex}"
        cursor = (ExecutionCursor.from_dict(resume_payload.get("execution_cursor", {}))
                  if _resume_state else ExecutionCursor())
        if not _resume_state:
            cursor.transition("planning")
        role_results = list(resume_payload.get("role_results", [])) if _resume_state else []
        role_stage_blockers = []
        if not _resume_state:
            self._save_task_checkpoint(task_id, "started", {"goal": user_prompt, "plan": [], "role_results": role_results}, cursor)
        self.lifecycle.transition_to(AgentState.PLANNING)
        self.memory.add_chat_message("user", ("Resume task: " if _resume_state else "") + user_prompt)
        log.info("user prompt received", extra={"fields": {"len": len(user_prompt)}})

        if self.watcher.check_for_updates():
            yield {"type": "status", "content": "File changes detected. Updating index..."}
            self.indexer.scan_workspace()

        context = self._build_context(user_prompt)
        if len(context) > 120_000:
            context = context[:120_000] + "\n... [context truncated] ..."

        # --- Phase 1: Plan or restore a validated persisted graph ---------
        task_graph = None
        if _resume_state:
            planned = True
            task_graph = self.planner.from_dict(resume_payload.get("task_graph", {}))
            task_graph_data = self.planner.to_dict(task_graph)
            ordered_ids = task_graph.topological_order()
            steps = [task_graph.nodes[node_id].description for node_id in ordered_ids]
            verification_strategy = next((str(item.get("text", "")) for item in role_results
                                          if item.get("role") == "tester"), "")
            architecture_brief = ""
            yield {"type": "status", "content": f"Restored task graph; {sum(n.status == 'completed' for n in task_graph.nodes.values())} nodes already completed."}
        else:
            # --- Phase 1: Plan (skipped for greetings and short questions) -----
            planned = should_plan(user_prompt, self.config.planning)
            steps: List[str] = []
            task_graph_data: Optional[Dict[str, Any]] = None
            architecture_brief = ""
            verification_strategy = ""
            if planned:
                if self.roles.has_independent_provider("architect"):
                    yield {"type": "status", "content": "Architect: analyzing design constraints..."}
                    try:
                        architect_result = await self.roles.analyze(
                            "architect", user_prompt, context, max_chars=9000)
                        architecture_brief = architect_result["text"]
                        role_results.append(architect_result)
                        self._save_task_checkpoint(task_id, "architect_completed", {"goal": user_prompt, "plan": steps, "role_results": role_results}, cursor)
                        yield {"type": "role_result", "content": architect_result}
                    except Exception as exc:
                        log.warning("architect stage unavailable", extra={"fields": {"error": str(exc)}})
                        yield {"type": "status", "content": "Architect unavailable; continuing with planner."}
                planner_input = user_prompt
                if architecture_brief:
                    planner_input += "\n\nArchitect brief (advisory):\n" + architecture_brief
                task_graph = await self.planner.create_plan(planner_input, context)
                task_graph_data = self.planner.to_dict(task_graph)
                ordered_ids = task_graph.topological_order()
                steps = [task_graph.nodes[node_id].description for node_id in ordered_ids]
                log.info("plan created", extra={"fields": {"steps": len(steps)}})
                plan_event = {"type": "plan", "content": steps}
                if task_graph.fallback_used:
                    warning = task_graph.fallback_warning
                    log.warning("planner fallback", extra={"fields": {"warning": warning}})
                    yield {"type": "status", "status": "planner_fallback", "content": warning}
                    plan_event.update({"status": "fallback", "warning": warning})
                yield plan_event
                if self.roles.has_independent_provider("tester"):
                    try:
                        tester_result = await self.roles.analyze(
                            "tester", user_prompt,
                            f"PLAN\n{steps}\n\nPROJECT CONTEXT\n{context}", max_chars=9000)
                        verification_strategy = tester_result["text"]
                        role_results.append(tester_result)
                        self._save_task_checkpoint(task_id, "tester_strategy_ready", {"goal": user_prompt, "plan": steps, "role_results": role_results}, cursor)
                        yield {"type": "role_result", "content": tester_result}
                    except Exception as exc:
                        role_stage_blockers.append(f"Tester strategy unavailable: {exc}")
                        log.warning("tester strategy unavailable", extra={"fields": {"error": str(exc)}})
            else:
                log.info("planner skipped", extra={"fields": {"mode": self.config.planning}})
                yield {"type": "status", "content": "Simple request: skipping the planning step."}

        if cursor.phase != "planned":
            cursor.transition("planned", mutation_started=False)
        task_graph_data = self.planner.to_dict(task_graph) if planned else (task_graph_data or {})
        self._save_task_checkpoint(task_id, "planned", {"goal": user_prompt, "plan": steps,
            "task_graph": task_graph_data or {}, "role_results": role_results}, cursor)

        # --- Phase 2: Execute each DAG node as an isolated unit ------------
        self.lifecycle.transition_to(AgentState.EXECUTING)
        system_prompt = (build_system_prompt(self.tool_engine.file_mutations_enabled)
                         + f"\n\n# CONTEXT\n{context}")
        final_box: List[str] = []
        fatal = [False]
        diff_summaries: List[str] = []
        review_diffs: List[str] = []
        journal = None

        if planned and task_graph is not None:
            # Deterministic sequential scheduler. Each node gets its own journal
            # owner and durable running/completed boundary; no parallel mutations.
            while not task_graph.is_completed():
                task_graph.mark_blocked_dependents()
                ready = task_graph.ready_nodes()
                if not ready:
                    break
                node = ready[0]
                node.status = "running"
                cursor.transition("executing", current_node_id=node.id, mutation_started=True)
                task_graph_data = self.planner.to_dict(task_graph)
                self._save_task_checkpoint(task_id, f"node_{node.id}_running", {
                    "goal": user_prompt, "plan": steps, "task_graph": task_graph_data,
                    "role_results": role_results, "test_results": [], "repair_attempt": 0}, cursor)
                dependency_context = "\n\n".join(
                    f"Completed prerequisite {dep}:\n{task_graph.nodes[dep].result[:3000]}"
                    for dep in node.dependencies)
                node_prompt = (
                    f"Original objective: {user_prompt}\n\n"
                    "Plan to follow in dependency-safe topological order; do not parallelize workspace mutations. Execute only the current ready node.\n"
                    f"Current DAG node: {node.id}\nTask: {node.description}\n\n"
                    f"Prerequisite evidence:\n{dependency_context or '(none)'}\n\n"
                    "Execute ONLY this node. Do not perform work belonging to other nodes. "
                    "When this node is complete, report concise evidence and stop. "
                    "Do not claim completion without checking the result.")
                if verification_strategy:
                    node_prompt += "\n\nProject verification guidance:\n" + verification_strategy
                node_messages = self.memory.get_chat_history() + [
                    {"role": "user", "content": node_prompt}]
                node_box: List[str] = []
                node_fatal = [False]
                journal_closed = False
                journal = self.tool_engine.create_mutation_journal(
                    owner_id=f"{task_id}-{node.id}",
                    checkpoint_enabled=self.config.checkpoint_mutations)
                try:
                    async for event in self._react_loop(
                            system_prompt, node_messages, node_box, node_fatal,
                            max_iterations=self.config.max_iterations, journal=journal,
                            allow_delegation=True, allow_parallel_delegation=False):
                        yield event
                    try:
                        review_diffs.append(journal.review_context(max_chars=30000))
                    except Exception:
                        review_diffs.append("")
                    node_diff = journal.close()
                    journal_closed = True
                    if node_diff:
                        diff_summaries.append(f"[{node.id}] {node_diff}")
                        yield {"type": "diff_summary", "content": f"[{node.id}] {node_diff}"}
                except Exception as exc:
                    node_fatal[0] = True
                    log.exception("DAG node execution or journal close failed",
                                  extra={"fields": {"task_id": task_id, "node_id": node.id}})
                    node_box.append(f"Node execution failed safely: {type(exc).__name__}")
                finally:
                    if not journal_closed:
                        try:
                            journal.close()
                            journal_closed = True
                        except Exception:
                            node_fatal[0] = True
                node_result = node_box[0] if node_box else ""
                verification_evidence = {"status": "not_configured", "results": [],
                                         "reason": "no node-specific verification commands were declared"}
                if journal_closed and not node_fatal[0]:
                    try:
                        verification_evidence = NodeVerificationPlan.execute(
                            self.tool_engine.builtin.sandbox, node.verification_commands)
                    except Exception as exc:
                        verification_evidence = {"status": "failed", "results": [],
                            "reason": f"verification plan rejected: {type(exc).__name__}: {str(exc)[:300]}"}
                    yield {"type": "node_verification", "content": {
                        "node_id": node.id, "verification": verification_evidence}}
                node.verification_evidence = verification_evidence
                acceptance = NodeAcceptanceContract.evaluate(
                    result=node_result, fatal=node_fatal[0], journal_closed=journal_closed,
                    verification=verification_evidence)
                if not acceptance.accepted:
                    node_fatal[0] = True
                if node_fatal[0]:
                    task_graph.update_status(node.id, "failed", result=node_result,
                                             error_log=acceptance.evidence,
                                             acceptance=acceptance.to_dict(),
                                             verification_evidence=verification_evidence)
                    task_graph.mark_blocked_dependents()
                    fatal[0] = True
                    cursor.transition("blocked", current_node_id=node.id, mutation_started=False)
                    task_graph_data = self.planner.to_dict(task_graph)
                    self._save_task_checkpoint(task_id, "node_failed", {
                        "goal": user_prompt, "plan": steps, "task_graph": task_graph_data,
                        "role_results": role_results, "test_results": [], "repair_attempt": 0}, cursor)
                    final_box.append(node_result)
                    break
                task_graph.update_status(node.id, "completed", result=node_result,
                                         acceptance=acceptance.to_dict(),
                                         verification_evidence=verification_evidence)
                cursor.complete_node(node.id)
                cursor.transition("planned", mutation_started=False)
                task_graph_data = self.planner.to_dict(task_graph)
                self._save_task_checkpoint(task_id, f"node_{node.id}_completed", {
                    "goal": user_prompt, "plan": steps, "task_graph": task_graph_data,
                    "role_results": role_results, "test_results": [], "repair_attempt": 0}, cursor)
                final_box.append(f"[{node.id}] {node_result}")
                yield {"type": "status", "content": f"DAG node completed: {node.id}"}
            if not task_graph.is_completed() and not fatal[0]:
                fatal[0] = True
                final_box.append("DAG scheduler stopped: no ready nodes remain; inspect dependency or failure state.")
            diff_summary = "\n\n".join(diff_summaries)
            review_diff = "\n\n".join(x for x in review_diffs if x)
        else:
            messages: List[Dict[str, Any]] = list(self.memory.get_chat_history())
            journal = self.tool_engine.create_mutation_journal(
                checkpoint_enabled=self.config.checkpoint_mutations)
            try:
                async for event in self._react_loop(
                        system_prompt, messages, final_box, fatal,
                        max_iterations=self.config.max_iterations, journal=journal):
                    yield event
                review_diff = journal.review_context(max_chars=30000)
                diff_summary = journal.close()
                if diff_summary:
                    yield {"type": "diff_summary", "content": diff_summary}
            finally:
                journal.close()

        final_text = final_box[0] if final_box else ""
        gate_blocked = bool(role_stage_blockers)
        if role_stage_blockers:
            final_text += "\n\nGATE BLOCKED: " + "; ".join(role_stage_blockers)
        cursor.transition("verification", mutation_started=False)
        self._save_task_checkpoint(task_id, "coder_completed", {"goal": user_prompt, "plan": steps,
            "task_graph": task_graph_data or {}, "role_results": role_results,
            "test_results": [], "repair_attempt": 0}, cursor)

        # Tester-owned execution is separate from the Coder's ReAct shell calls.
        test_contract = parse_test_commands(verification_strategy) if verification_strategy else {
            "status": "inconclusive", "commands": [], "reason": "no independent Tester strategy available"}
        if test_contract["status"] == "ready":
            yield {"type": "status", "content": "Tester: running independent verification in OS sandbox..."}
            test_results = TesterRunner(self.tool_engine.builtin.sandbox).run(test_contract["commands"])
            yield {"type": "test_result", "content": test_results}
            if test_results["status"] != "passed":
                gate_blocked = True
                final_text += "\n\nGATE BLOCKED: independent Tester verification did not pass."
        else:
            test_results = {"status": "inconclusive", "reason": test_contract["reason"], "results": []}
            yield {"type": "test_result", "content": test_results}
            if verification_strategy:
                gate_blocked = True
                final_text += "\n\nGATE BLOCKED: Tester output was not executable structured evidence."
        cursor.transition("review", mutation_started=True)
        self._save_task_checkpoint(task_id, "tester_completed", {"goal": user_prompt, "plan": steps,
            "task_graph": task_graph_data or {}, "role_results": role_results,
            "test_results": test_results, "repair_attempt": 0}, cursor)

        # Independent, read-only post-change gates. They inspect the journal's
        # change summary; they do not execute commands or modify workspace files.
        if diff_summary:
            if not review_diff:
                review_diff = "(textual diff unavailable; use the journal summary only)"
            audit_context = (
                f"USER OBJECTIVE\n{user_prompt}\n\nCHANGE JOURNAL SUMMARY\n{diff_summary}"
                f"\n\nBOUNDED CHANGE DIFF\n{review_diff}"
                f"\n\nCODER REPORT\n{final_text}"
            )
            for role in ("reviewer", "security_auditor"):
                if not self.roles.has_independent_provider(role):
                    continue
                try:
                    yield {"type": "status", "content": f"{role.replace('_', ' ').title()}: independent review..."}
                    audit = await self.roles.analyze(role, user_prompt, audit_context, max_chars=14000)
                    if role in {"reviewer", "security_auditor"}:
                        role_results.append(audit)
                        gate = parse_review_findings(audit.get("text", ""))
                        audit["gate"] = gate
                        if gate["blocking"]:
                            gate_blocked = True
                            final_text += f"\n\nGATE BLOCKED: {role} — {gate['reason']}. Do not mark this task complete."
                        self._save_task_checkpoint(task_id, f"{role}_completed", {"goal": user_prompt,
                            "plan": steps, "task_graph": task_graph_data or {}, "role_results": role_results,
                            "test_results": test_results, "repair_attempt": 0}, cursor)
                    yield {"type": "role_result", "content": audit}
                    if audit.get("text"):
                        final_text += f"\n\n## {role.replace('_', ' ').title()}\n{audit['text']}"
                except Exception as exc:
                    gate_blocked = True
                    final_text += f"\n\nGATE BLOCKED: {role.replace('_', ' ').title()} review unavailable."
                    log.warning("post-change role failed", extra={"fields": {"role": role, "error": str(exc)}})
                    yield {"type": "status", "content": f"{role.replace('_', ' ').title()} review unavailable: {exc}"}

        cursor.transition("blocked" if gate_blocked else "completed", mutation_started=False)
        self._save_task_checkpoint(task_id, "blocked" if gate_blocked else "completed", {"goal": user_prompt, "plan": steps,
            "task_graph": task_graph_data or {}, "role_results": role_results,
            "test_results": test_results, "repair_attempt": 0}, cursor)
        self.memory.add_chat_message("assistant", final_text)
        self.save_session()
        self.lifecycle.transition_to(AgentState.IDLE)
        yield {"type": "done", "status": "blocked" if gate_blocked else "completed", "content": final_text}

    # ------------------------------------------------------------------
    # The ReAct loop
    # ------------------------------------------------------------------
    async def _react_loop(self, system_prompt: str, messages: List[Dict[str, Any]],
                          final_box: List[str], fatal: List[bool],
                          max_iterations: int = 15,
                          *, journal: MutationJournal,
                          allow_delegation: bool = True,
                          allow_parallel_delegation: bool = True) -> AsyncGenerator[Dict[str, Any], None]:
        """Run the agent loop until the model stops calling tools.

        Events are yielded for the UI; the accumulated assistant text is stored
        in ``final_box`` (a single-element list) so the caller can retrieve it.
        ``fatal`` is a single-element flag list set to True on unrecoverable
        failure. Failed operations may roll back only their own journaled delta;
        successful task edits are never restored by a loop-level failure.
        """
        text_parts: List[str] = []
        coder_provider = self.roles.provider_for("coder")
        use_tools = getattr(coder_provider, "supports_tools", True)
        tool_schemas = self.tool_engine.get_tool_schemas() if use_tools else None
        if tool_schemas is not None and not allow_delegation:
            tool_schemas = [schema for schema in tool_schemas
                            if schema.get("function", {}).get("name") != "delegate_tasks"]

        last_call_key: Optional[str] = None
        consecutive_repeats = 0
        successful_task_mutation = False
        verification_pending = False
        repair_pass_used = False

        for iteration in range(max_iterations):
            # Anti-loop: too many repeats of the same tool call.
            if consecutive_repeats >= self.config.max_repeated_tool_calls:
                yield {"type": "status",
                       "content": f"Tool call repeated {self.config.max_repeated_tool_calls}+ times "
                                  "consecutively without progress. Stopping to avoid an infinite loop."}
                fatal[0] = True
                break

            yield {"type": "status", "content": f"Iteration {iteration + 1}"}
            assistant_text = ""
            tool_calls: List[Dict[str, Any]] = []
            stream_failed = False

            try:
                async for chunk in self._stream_with_retry(
                    coder_provider, system_prompt, messages, tools=tool_schemas
                ):
                    ctype = chunk.get("type")
                    if ctype == "text":
                        content = chunk.get("content", "")
                        assistant_text += content
                        text_parts.append(content)
                        yield {"type": "text", "content": content}
                    elif ctype == "tool_call":
                        tool_calls.append(chunk.get("content", {}))
                    elif ctype == "status":
                        yield chunk
                    elif ctype == "error":
                        stream_failed = True
                        if tool_calls or assistant_text:
                            # Partial progress; hand control back to the model.
                            messages.append({
                                "role": "user",
                                "content": f"[STREAM ERROR] {chunk.get('content', '')}\n"
                                           "Continue using the tools; retry the failed step.",
                            })
                        else:
                            yield {"type": "error", "content": chunk.get("content", "Provider error")}
                            final_box.append("".join(text_parts))
                            fatal[0] = True
                            return
            except Exception as e:
                yield {"type": "error", "content": f"Unrecoverable provider error: {e}"}
                final_box.append("".join(text_parts))
                fatal[0] = True
                return

            messages.append({"role": "assistant", "content": assistant_text or None})
            if tool_calls:
                messages[-1]["tool_calls"] = [
                    {"id": tc.get("id", f"call_{i}"), "type": "function",
                     "function": {"name": tc.get("name", ""),
                                  "arguments": json.dumps(tc.get("arguments", {}), ensure_ascii=False)}}
                    for i, tc in enumerate(tool_calls)
                ]

            # Fallback: markdown protocol blocks (routers without tool support).
            if not tool_calls and not stream_failed and assistant_text:
                md_results = await self.tool_engine.execute_markdown_actions(
                    assistant_text, journal=journal)
                if md_results:
                    successful_task_mutation = (
                        successful_task_mutation or journal.has_successful_mutations)
                    md_lower = md_results.lower()
                    markdown_verify_failed = (
                        "[bash]" in md_lower
                        and ("(exit code " in md_lower or "timeout:" in md_lower)
                        and "authorization" not in md_lower
                        and "journal/checkpoint" not in md_lower
                    )
                    if markdown_verify_failed and successful_task_mutation:
                        if repair_pass_used:
                            yield {"type": "status",
                                   "content": "Verification still fails after one repair pass; successful edits are preserved."}
                            final_box.append("".join(text_parts))
                            fatal[0] = True
                            yield {"type": "error",
                                   "content": "Task stopped after the repair pass. Review the final diff summary and undo material."}
                            return
                        repair_pass_used = True
                        verification_pending = True
                        messages.append({"role": "user", "content": self.repairer.build_repair_instruction(
                            md_results, attempt=1, max_attempts=1, tool_name="markdown shell action")})
                        yield {"type": "status",
                               "content": "Verification failed; one repair pass is available (evidence-based). Successful edits will be kept."}
                    elif verification_pending and "[bash]" in md_lower and "(exit code " not in md_lower:
                        verification_pending = False
                        yield {"type": "status", "content": "Verification passed; task edits are preserved."}
                    yield {"type": "status", "content": "Executed Markdown actions with task journal."}
                    messages.append({
                        "role": "user",
                        "content": f"[SYSTEM EXECUTION LOG]\n"
                                   f"{truncate_middle(md_results, self.config.max_tool_output_chars * 2)}\n\n"
                                   "If errors occurred, fix them. Otherwise continue and finish the task.",
                    })
                    text_parts.append(f"\n[executed {len(md_results.splitlines())} action lines]\n")
                    continue

            if not tool_calls:
                break  # model finished; no more tool calls

            # Several read-only calls in one turn run concurrently; results are
            # still reported (and counted by the loop guard) in model order.
            precomputed: Dict[int, Dict[str, Any]] = {}
            if len(tool_calls) > 1 and all(
                    (tc.get("name") or "") in READ_ONLY_TOOLS for tc in tool_calls):
                outcomes = await asyncio.gather(
                    *(self.tool_engine.execute_tool(
                        tc.get("name", ""), tc.get("arguments", {}) or {}, journal=journal)
                      for tc in tool_calls),
                    return_exceptions=True)
                for idx, outcome in enumerate(outcomes):
                    if isinstance(outcome, BaseException):
                        outcome = {"tool": tool_calls[idx].get("name", ""),
                                   "status": "error", "output": f"Error: {outcome}"}
                    precomputed[idx] = outcome

            for idx, tc in enumerate(tool_calls):
                name = tc.get("name", "")
                args = tc.get("arguments", {}) or {}
                try:
                    arg_key = json.dumps(args, sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=False, default=str)
                except (TypeError, ValueError):
                    arg_key = repr(args)
                call_key = f"{name}:{arg_key}"
                consecutive_repeats = (consecutive_repeats + 1
                                       if call_key == last_call_key else 1)
                last_call_key = call_key

                if name == "delegate_tasks":
                    call_id = str(tc.get("id", f"call_{iteration}_{idx}"))
                    if not allow_delegation:
                        output = "Parallel sub-agent delegation is disabled inside the sequential DAG scheduler."
                        messages.append({"role": "tool", "tool_call_id": call_id, "content": output})
                        yield {"type": "tool_result", "content": {"name": name, "status": "error", "output_preview": output}}
                        continue
                    tasks = args.get("tasks", []) or []
                    if not isinstance(tasks, list) or not tasks:
                        messages.append({"role": "tool", "tool_call_id": call_id,
                                         "content": "delegate_tasks requires a non-empty 'tasks' array."})
                        continue
                    try:
                        claim = (journal.claim_tool_invocation(call_id, name, args)
                                 if hasattr(journal, "claim_tool_invocation") else {"claimed": True})
                    except Exception as exc:
                        output = f"Delegation rejected by idempotency journal: {exc}"
                        messages.append({"role": "tool", "tool_call_id": call_id, "content": output})
                        yield {"type": "tool_result", "content": {"name": name, "status": "error", "output_preview": output}}
                        continue
                    if not claim.get("claimed"):
                        output = ("Duplicate delegation suppressed; previous invocation state is "
                                  f"{claim.get('status', 'uncertain')}. Inspect workspace before retrying.")
                        messages.append({"role": "tool", "tool_call_id": call_id, "content": output})
                        yield {"type": "tool_result", "content": {"name": name, "status": "error", "output_preview": output}}
                        continue
                    yield {"type": "tool_call", "content": {"name": name, "arguments": args}}
                    mode = "in parallel" if allow_parallel_delegation else "sequentially"
                    yield {"type": "status", "content": f"Delegating {len(tasks)} sub-agent tasks {mode}..."}
                    try:
                        results = await self.subagents.run_many(tasks, parallel=allow_parallel_delegation)
                        summary = self.subagents.format_results(results)
                        if hasattr(journal, "finish_tool_invocation"):
                            journal.finish_tool_invocation(call_id, "ok")
                    except Exception:
                        if hasattr(journal, "finish_tool_invocation"):
                            journal.finish_tool_invocation(call_id, "error")
                        raise
                    yield {"type": "tool_result", "content": {
                        "name": name, "status": "ok", "output_preview": summary[:3000],
                    }}
                    messages.append({"role": "tool", "tool_call_id": call_id, "content": summary[:6000]})
                    continue

                yield {"type": "tool_call", "content": {"name": name, "arguments": args}}
                result = precomputed.get(idx)
                if result is None:
                    if name in MUTATING_TOOLS and hasattr(journal, "claim_tool_invocation"):
                        call_id = str(tc.get("id", f"call_{iteration}_{idx}"))
                        claimed = False
                        try:
                            claim = journal.claim_tool_invocation(call_id, name, args)
                            claimed = bool(claim.get("claimed"))
                            if not claimed:
                                result = {"tool": name, "status": "error",
                                    "output": ("Duplicate mutating invocation suppressed. "
                                        "The earlier call is recorded as "
                                        f"{claim.get('status', 'uncertain')}; inspect workspace state "
                                        "before attempting another mutation.")}
                            else:
                                result = await self.tool_engine.execute_tool(name, args, journal=journal)
                                journal.finish_tool_invocation(call_id, result.get("status", "ok"))
                        except Exception as exc:
                            if claimed:
                                try:
                                    journal.finish_tool_invocation(call_id, "error")
                                except Exception:
                                    pass
                            result = {"tool": name, "status": "error",
                                      "output": f"Mutation invocation failed safely: {exc}"}
                    else:
                        result = await self.tool_engine.execute_tool(name, args, journal=journal)
                status = result.get("status", "ok")
                output = result.get("output") or ""
                log.info("tool executed",
                         extra={"fields": {"tool": name, "status": status,
                                           "output_len": len(output)}})
                try:
                    self.plugins.dispatch_event({"type": "tool_result", "name": name,
                                                 "status": status})
                except Exception:
                    pass
                yield {"type": "tool_result", "content": {
                    "name": name,
                    "status": status,
                    "output_preview": output[:3000],
                }}
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", f"call_{iteration}_{len(tool_calls)}"),
                    "content": truncate_middle(output, self.config.max_tool_output_chars) or status,
                })

                if status == "ok":
                    if self.config.store_tool_memory and name in MUTATING_TOOLS:
                        try:
                            self.memory.store_knowledge(
                                f"tool:{name}:{int(time.time())}",
                                f"Action '{name}' succeeded. Summary: {output[:300]}",
                            )
                        except Exception:
                            pass
                if name in MUTATING_TOOLS and status == "ok":
                    successful_task_mutation = (
                        successful_task_mutation or journal.has_successful_mutations)

                output_lower = output.lower()
                verification_failure = (
                    name == "run_command" and status != "ok"
                    and ("(exit code " in output_lower or "timeout:" in output_lower)
                    and "authorization" not in output_lower
                    and "journal/checkpoint" not in output_lower
                )
                if verification_failure and successful_task_mutation:
                    if repair_pass_used:
                        yield {"type": "status",
                               "content": "Verification still fails after one repair pass; successful edits are preserved."}
                        fatal[0] = True
                        final_box.append("".join(text_parts))
                        yield {"type": "error",
                               "content": "Task stopped after the repair pass. Review the final diff summary and manual undo material."}
                        return
                    repair_pass_used = True
                    verification_pending = True
                    messages.append({"role": "user", "content": self.repairer.build_repair_instruction(
                        output, attempt=1, max_attempts=1, tool_name=name)})
                    yield {"type": "status",
                           "content": "Verification failed; one repair pass is available (evidence-based). Successful edits will be kept."}
                elif name == "run_command" and status == "ok" and verification_pending:
                    verification_pending = False
                    yield {"type": "status", "content": "Verification passed; task edits are preserved."}

            # Compact if the context buffer is growing too large.
            messages = self._compact_messages(messages, system_prompt)
        else:
            fatal[0] = True
            yield {"type": "status", "content": "Max iterations reached. Node cannot be marked complete."}

        if verification_pending:
            fatal[0] = True
            yield {"type": "status",
                   "content": "Verification remains unresolved; successful edits were preserved for review and manual undo. Node cannot be marked complete."}

        final_box.append("".join(text_parts))
