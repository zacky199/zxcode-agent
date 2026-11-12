import os
import re
import subprocess
import warnings
from pathlib import Path
from typing import Any, Callable, List

from execution.diff_engine import DiffEngine
from execution.sandbox import SandboxExecutor
from utils.filesystem import FileSystemUtils
from utils.sensitive_files import is_sensitive_path
from tools.mutation_policy import (
    FILE_MUTATION_DISABLED_MESSAGE,
    NATIVE_FILE_MUTATIONS_DEFAULT,
)


class BuiltinTools:
    """Primitive tool implementations the agent can invoke."""

    IGNORE_DIRS = {
        ".git", "node_modules", "__pycache__", "venv", "env", "dist",
        "build", ".zx_checkpoints", ".next", ".nuxt", ".cache", "coverage",
    }

    def __init__(self, workspace: Path, command_authorizer: Callable[[str], bool] = None,
                 allow_file_mutations: bool = NATIVE_FILE_MUTATIONS_DEFAULT,
                 sandbox_mode: str = "container", allow_host_execution: bool = False,
                 sandbox_runtime: str = None, sandbox_image: str = "zxcode-sandbox:latest",
                 sandbox_network: str = "none"):

        self.workspace = workspace
        # Set only by trusted code (CLI flag or /edits); never from tool arguments.
        self.allow_file_mutations = bool(allow_file_mutations)
        self.sandbox = SandboxExecutor(workspace, command_authorizer=command_authorizer,
            mode=sandbox_mode, allow_host_execution=allow_host_execution,
            runtime=sandbox_runtime, image=sandbox_image, network=sandbox_network)
        self.diff_engine = DiffEngine(workspace)

    def _safe(self, target: Path) -> bool:
        return FileSystemUtils.is_within_workspace(target, self.workspace)

    @staticmethod
    def _is_sensitive_mutation_path(requested: Path, resolved: Path) -> bool:
        return is_sensitive_path(requested) or is_sensitive_path(resolved)

    def _is_visible_workspace_path(self, path: Path) -> bool:
        requested = Path(path)
        candidate = requested if requested.is_absolute() else self.workspace / requested
        try:
            if (is_sensitive_path(requested) or is_sensitive_path(candidate)
                    or not self._safe(candidate) or candidate.is_symlink()):
                return False
            if (candidate.is_file()
                    and FileSystemUtils.is_hard_linked_regular_file(candidate)):
                return False
            return True
        except (OSError, RuntimeError, ValueError):
            return False

    # --- file tools -------------------------------------------------
    def read_file(self, file_path: str, start_line=None, end_line=None) -> str:
        target = (self.workspace / file_path).resolve()
        if not self._safe(target):
            return f"Error: Access denied. {file_path} is outside the workspace."
        if is_sensitive_path(self.workspace / file_path) or is_sensitive_path(target):
            return "Error: Access denied. Credential-like files are not available to the model."
        if not target.exists():
            return f"Error: File {file_path} not found."
        if target.is_dir():
            return f"Error: {file_path} is a directory, not a file."
        if FileSystemUtils.is_hard_linked_regular_file(target):
            return "Error: Access denied. Hard-linked files are not available to the model."
        try:
            content = target.read_text(encoding="utf-8")
            lines = content.splitlines()
            total = len(lines)
            if start_line is not None or end_line is not None:
                return self._read_line_range(lines, start_line, end_line)
            if total > self.MAX_READ_LINES:
                content = "\n".join(lines[:self.MAX_READ_LINES])
                content += (f"\n... [truncated: file has {total} lines; "
                            f"call read_file with start_line={self.MAX_READ_LINES + 1} to continue] ...")
            return content
        except UnicodeDecodeError:
            return f"Error: {file_path} appears to be binary and cannot be read as text."
        except Exception as e:
            return f"Error reading file: {e}"

    MAX_READ_LINES = 2000
    MAX_COMMAND_TIMEOUT = 900.0

    def _read_line_range(self, lines: List[str], start_line, end_line) -> str:
        total = len(lines)
        try:
            start = int(start_line) if start_line is not None else 1
            end = int(end_line) if end_line is not None else start + self.MAX_READ_LINES - 1
        except (TypeError, ValueError):
            return "Error: start_line and end_line must be integers."
        if start < 1 or end < start:
            return "Error: invalid line range (start_line must be >= 1 and end_line >= start_line)."
        if total == 0:
            return "[lines 0-0 of 0]\n(empty file)"
        if start > total:
            return f"Error: start_line {start} is beyond the end of the file ({total} lines)."
        end = min(end, total, start + self.MAX_READ_LINES - 1)
        body = "\n".join(lines[start - 1:end])
        footer = (f"\n... [{total - end} more lines; continue with start_line={end + 1}] ..."
                  if end < total else "")
        return f"[lines {start}-{end} of {total}]\n{body}{footer}"

    def list_directory(self, path: str = ".") -> str:
        target = (self.workspace / path).resolve()
        if not self._safe(target):
            return f"Error: Access denied. Path {path} is outside the workspace."
        if not target.exists() or not target.is_dir():
            return f"Error: Directory {path} not found."
        try:
            items = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            lines = []
            for item in items:
                if not self._is_visible_workspace_path(item):
                    continue
                name = item.name
                if item.is_dir():
                    lines.append(f"📁 {name}/")
                else:
                    size = item.stat().st_size if item.is_file() else 0
                    lines.append(f"📄 {name} ({size} B)")
            return "\n".join(lines) if lines else "(empty directory)"
        except Exception as e:
            return f"Error listing directory: {e}"

    def write_file(self, file_path: str, content: str,
                   before_replace=None, retain_displaced=None,
                   parent_anchor=None) -> str:
        if not self.allow_file_mutations:
            return FILE_MUTATION_DISABLED_MESSAGE
        requested = self.workspace / file_path
        target = requested.resolve(strict=False)
        if not self._safe(target):
            return f"Error: Access denied. {file_path} is outside the workspace."
        if self._is_sensitive_mutation_path(requested, target):
            return "Error: Access denied. Credential-like files cannot be modified."
        if (not callable(before_replace) or not callable(retain_displaced)
                or parent_anchor is None):
            return "Error: Access denied. Native file writes require the ToolEngine journal gate."
        try:
            safe_target = self.workspace / parent_anchor.target_relative
            FileSystemUtils.atomic_write(
                safe_target, content, before_replace=before_replace,
                retain_displaced=retain_displaced, parent_anchor=parent_anchor)
            return f"Success: wrote {file_path} ({len(content)} chars)."
        except Exception as e:
            return f"Error writing file: {e}"

    def edit_file(self, file_path: str, search: str, replace: str,
                  before_replace=None, retain_displaced=None,
                  parent_anchor=None) -> str:
        if not self.allow_file_mutations:
            return FILE_MUTATION_DISABLED_MESSAGE
        requested = self.workspace / file_path
        target = requested.resolve(strict=False)
        if not self._safe(target):
            return f"Error: Access denied. {file_path} is outside the workspace."
        if self._is_sensitive_mutation_path(requested, target):
            return "Error: Access denied. Credential-like files cannot be modified."
        if (not callable(before_replace) or not callable(retain_displaced)
                or parent_anchor is None):
            return "Error: Access denied. Native file edits require the ToolEngine journal gate."
        safe_file_path = parent_anchor.target_relative
        return self.diff_engine.apply_patch(
            str(safe_file_path), search, replace, before_replace=before_replace,
            retain_displaced=retain_displaced, parent_anchor=parent_anchor)

    # --- search tools ------------------------------------------------
    def search_files(self, pattern: str, path: str = ".") -> str:
        requested_target = self.workspace / path
        target = requested_target.resolve()
        if not self._safe(target):
            return f"Error: Access denied. Path {path} is outside the workspace."
        if is_sensitive_path(requested_target) or is_sensitive_path(target):
            return "Error: Access denied. Credential-like files are not available to the model."
        if not target.exists():
            return f"Error: Path {path} not found."
        if target.is_file() and FileSystemUtils.is_hard_linked_regular_file(target):
            return "Error: Access denied. Hard-linked files are not available to the model."
        try:
            regex = re.compile(pattern)
        except re.error as e:
            return f"Error: invalid regex: {e}"

        matches: List[str] = []
        workspace_root = self.workspace.resolve()
        try:
            for root, dirs, files in os.walk(target):
                root_path = Path(root)
                dirs[:] = [
                    d for d in dirs
                    if d not in self.IGNORE_DIRS and not d.startswith(".")
                    and not (root_path / d).is_symlink()
                    and not is_sensitive_path(root_path / d)
                ]
                for fname in files:
                    fpath = root_path / fname
                    if fpath.is_symlink() or is_sensitive_path(fpath):
                        continue
                    try:
                        resolved = fpath.resolve()
                        resolved.relative_to(workspace_root)
                        if (is_sensitive_path(resolved)
                                or FileSystemUtils.is_hard_linked_regular_file(resolved)):
                            continue
                        rel = str(resolved.relative_to(workspace_root))
                    except (OSError, ValueError):
                        continue
                    if resolved.stat().st_size > 500_000:
                        continue
                    try:
                        with open(resolved, "r", encoding="utf-8", errors="ignore") as fh:
                            for lineno, line in enumerate(fh, 1):
                                if regex.search(line):
                                    matches.append(f"{rel}:{lineno}: {line.rstrip()[:180]}")
                    except Exception:
                        continue
                    if len(matches) > 200:
                        matches.append("... [truncated: too many matches]")
                        return "\n".join(matches)
        except Exception as e:
            return f"Error searching files: {e}"
        return "\n".join(matches) if matches else "No matches found."

    def glob_files(self, pattern: str) -> str:
        try:
            results = sorted(str(p.relative_to(self.workspace))
                             for p in self.workspace.rglob(pattern)
                             if not any(part in self.IGNORE_DIRS or part.startswith(".")
                                        for part in p.relative_to(self.workspace).parts)
                             and self._is_visible_workspace_path(p))
            return "\n".join(results[:200]) if results else "No files matched."
        except Exception as e:
            return f"Error globbing files: {e}"

    # --- command / git tools -----------------------------------------
    def execute_command(self, command: str, timeout=None) -> str:
        exec_timeout = None
        if timeout is not None:
            try:
                exec_timeout = max(1.0, min(float(timeout), self.MAX_COMMAND_TIMEOUT))
            except (TypeError, ValueError):
                return "Error: timeout must be a number of seconds."
        code, stdout, stderr = self.sandbox.run_command(command, timeout=exec_timeout)
        parts = []
        if stdout:
            parts.append(stdout)
        if stderr:
            parts.append(stderr.strip())
        if code != 0:
            parts.append(f"(exit code {code})")
        return "\n".join(parts) if parts else "(no output)"

    def git_status(self) -> str:
        try:
            result = subprocess.run(
                ["git", "status", "--short", "--branch", "-z"],
                cwd=self.workspace, capture_output=True, timeout=30,
            )
            if result.returncode != 0:
                error = os.fsdecode(result.stderr or b"").strip()
                return f"git error: {error}" if error else "(no output)"

            tokens = (result.stdout or b"").split(b"\0")
            lines = []
            index = 0
            while index < len(tokens):
                record = tokens[index]
                index += 1
                if not record:
                    continue
                if record.startswith(b"## "):
                    lines.append(os.fsdecode(record))
                    continue
                if len(record) < 3:
                    continue
                status = os.fsdecode(record[:2])
                path = os.fsdecode(record[3:])
                original_path = None
                if "R" in status or "C" in status:
                    if index < len(tokens):
                        original_path = os.fsdecode(tokens[index])
                        index += 1
                if (not self._is_visible_workspace_path(Path(path))
                        or (original_path and not self._is_visible_workspace_path(
                            Path(original_path)))):
                    continue
                lines.append(f"{status} {path}")
            return "\n".join(lines) if lines else "(no output)"
        except FileNotFoundError:
            return "Error: git is not installed."
        except subprocess.TimeoutExpired:
            return "Error: git command timed out."

    def git_diff(self) -> str:
        try:
            changed = subprocess.run(
                ["git", "--literal-pathspecs", "diff", "--no-ext-diff", "--no-renames", "--name-only", "-z"],
                cwd=self.workspace, capture_output=True, timeout=30,
            )
            if changed.returncode != 0:
                error = os.fsdecode(changed.stderr or b"").strip()
                return f"git error: {error}" if error else "(no output)"
            paths = [os.fsdecode(item) for item in (changed.stdout or b"").split(b"\0") if item]
            safe_paths = [path for path in paths
                          if self._is_visible_workspace_path(Path(path))]
            if not safe_paths:
                return "(no output)"

            stat_result = subprocess.run(
                ["git", "--literal-pathspecs", "diff", "--no-ext-diff", "--stat", "--", *safe_paths],
                cwd=self.workspace, capture_output=True, text=True, timeout=30,
            )
            if stat_result.returncode != 0:
                error = (stat_result.stderr or "").strip()
                return f"git error: {error}" if error else "(no output)"
            summary = (stat_result.stdout or "").strip()
            if summary:
                return summary

            diff_result = subprocess.run(
                ["git", "--literal-pathspecs", "diff", "--no-ext-diff", "--", *safe_paths],
                cwd=self.workspace, capture_output=True, text=True, timeout=30,
            )
            if diff_result.returncode != 0:
                error = (diff_result.stderr or "").strip()
                return f"git error: {error}" if error else "(no output)"
            return (diff_result.stdout or "").strip() or "(no output)"
        except FileNotFoundError:
            return "Error: git is not installed."
        except subprocess.TimeoutExpired:
            return "Error: git command timed out."

    def _git(self, args: List[str]) -> str:
        try:
            result = subprocess.run(
                ["git", *args], cwd=self.workspace,
                capture_output=True, text=True, timeout=30,
            )
            out = (result.stdout or "").strip()
            err = (result.stderr or "").strip()
            return out if out else (f"git error: {err}" if err else "(no output)")
        except FileNotFoundError:
            return "Error: git is not installed."
        except subprocess.TimeoutExpired:
            return "Error: git command timed out."


def register_builtin_tools(registry: Any, workspace: Path,
                           command_authorizer: Callable[[str], bool] = None) -> None:
    """Deprecated read-only bridge; mutating tools must be registered by ToolEngine."""
    warnings.warn(
        "register_builtin_tools is deprecated and registers read-only tools only; "
        "use ToolEngine for journal-gated mutations.",
        DeprecationWarning, stacklevel=2,
    )
    builtin = BuiltinTools(workspace, command_authorizer=command_authorizer)
    registry.register("read_file", builtin.read_file)
    registry.register("list_directory", builtin.list_directory)
    registry.register("search_files", builtin.search_files)
    registry.register("glob_files", builtin.glob_files)
    registry.register("git_status", builtin.git_status)
    registry.register("git_diff", builtin.git_diff)
