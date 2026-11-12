"""Task-scoped mutation journals with conservative, ownership-checked rollback.

This journal is recovery bookkeeping, not an OS sandbox. Shell and plugin code
still executes with the privileges of the ZX Code process.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from config.constants import CHECKPOINT_DIR_NAME
from utils.filesystem import (
    DirectoryIdentityError,
    FileSystemUtils,
    ParentDirectoryAnchor,
    ParentDirectoryState,
)
from utils.sensitive_files import is_sensitive_path


class JournalError(RuntimeError):
    """A checkpoint/journal could not safely authorize a mutation."""


@dataclass
class PathState:
    kind: str
    sha256: str = ""
    mode: int = 0
    size: int = 0
    device: int = 0
    inode: int = 0
    mtime_ns: int = 0
    link_target: str = ""
    backup: str = ""

    def matches(self, other: Optional["PathState"]) -> bool:
        if other is None or self.kind != other.kind:
            return False
        if self.kind == "missing":
            return True
        if self.kind == "file":
            return (self.sha256, self.mode, self.size, self.device, self.inode,
                    self.mtime_ns) == (
                other.sha256, other.mode, other.size, other.device,
                other.inode, other.mtime_ns,
            )
        if self.kind == "directory":
            return (self.mode, self.device, self.inode) == (
                other.mode, other.device, other.inode)
        if self.kind == "symlink":
            return (self.link_target, self.device, self.inode, self.mtime_ns) == (
                other.link_target, other.device, other.inode, other.mtime_ns)
        return False

    def equivalent(self, other: Optional["PathState"]) -> bool:
        """Compare user-visible state, ignoring inode/mtime churn on same content."""
        if other is None or self.kind != other.kind:
            return False
        if self.kind == "missing":
            return True
        if self.kind == "file":
            return (self.sha256, self.mode, self.size) == (
                other.sha256, other.mode, other.size)
        if self.kind == "directory":
            return self.mode == other.mode
        if self.kind == "symlink":
            return self.link_target == other.link_target
        return False

    def manifest(self, root: Path) -> Dict[str, Any]:
        result = {
            "kind": self.kind,
            "sha256": self.sha256,
            "mode": self.mode,
            "size": self.size,
            "device": self.device,
            "inode": self.inode,
            "mtime_ns": self.mtime_ns,
            "link_target": self.link_target,
        }
        if self.backup:
            try:
                result["backup"] = str(Path(self.backup).relative_to(root))
            except ValueError:
                result["backup"] = ""
        return result


@dataclass
class MutationTicket:
    operation_id: str
    tool_name: str
    owner_id: str
    scope: str
    paths: List[str]
    before: Dict[str, PathState]
    claimed_paths: List[str] = field(default_factory=list)
    workspace_claimed: bool = False
    expected_outputs: Dict[str, str] = field(default_factory=dict)
    precommit_conflicts: List[str] = field(default_factory=list)
    retained_preimages: List[str] = field(default_factory=list)
    parent_states_before: Dict[str, ParentDirectoryState] = field(default_factory=dict)
    parent_states_after: Dict[str, ParentDirectoryState] = field(default_factory=dict)
    parent_anchor: Optional[ParentDirectoryAnchor] = field(default=None, repr=False)
    ended: bool = False


@dataclass
class JournalChange:
    path: str
    before: PathState
    after: PathState


@dataclass
class JournalOperation:
    ticket: MutationTicket
    changes: List[JournalChange]
    capture_error: str = ""
    ambiguous_paths: Set[str] = field(default_factory=set)


@dataclass
class RollbackReport:
    restored: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.skipped

    def describe(self) -> str:
        details = []
        if self.restored:
            details.append("restored: " + ", ".join(self.restored))
        if self.skipped:
            details.append("preserved (uncertain/changed): " + "; ".join(self.skipped))
        return "; ".join(details) or "no file changes to restore"


class MutationCoordinator:
    """In-process ownership gate shared by parent and child task journals."""

    def __init__(self):
        self._lock = threading.Lock()
        self._path_owners: Dict[str, str] = {}
        self._workspace_owner: Optional[str] = None

    @staticmethod
    def _overlaps(left: str, right: str) -> bool:
        left_path = PurePosixPath(left)
        right_path = PurePosixPath(right)
        return (left_path == right_path or left_path in right_path.parents
                or right_path in left_path.parents)

    def claim_paths(self, owner_id: str, paths: Sequence[str]) -> None:
        normalized = tuple(dict.fromkeys(paths))
        with self._lock:
            if self._workspace_owner is not None:
                raise JournalError("workspace is owned by another active mutation")
            conflicts = [existing for existing in self._path_owners
                         if any(self._overlaps(existing, path) for path in normalized)]
            if conflicts:
                raise JournalError(
                    "overlapping path already owned by an active operation: "
                    + ", ".join(conflicts[:3]))
            for path in normalized:
                self._path_owners[path] = owner_id

    def claim_workspace(self, owner_id: str) -> None:
        with self._lock:
            if self._workspace_owner is not None:
                raise JournalError("workspace is owned by another active mutation")
            conflicts = list(self._path_owners)
            if conflicts:
                raise JournalError(
                    "workspace has paths owned by another active task: "
                    + ", ".join(conflicts[:3]))
            self._workspace_owner = owner_id

    def release_paths(self, owner_id: str, paths: Iterable[str]) -> None:
        with self._lock:
            for path in paths:
                if self._path_owners.get(path) == owner_id:
                    self._path_owners.pop(path, None)

    def release_workspace(self, owner_id: str) -> None:
        with self._lock:
            if self._workspace_owner == owner_id:
                self._workspace_owner = None

    def release_owner(self, owner_id: str) -> None:
        with self._lock:
            self._path_owners = {
                path: owner for path, owner in self._path_owners.items()
                if owner != owner_id
            }
            if self._workspace_owner == owner_id:
                self._workspace_owner = None


class MutationJournal:
    """Persist pre/post state for one task; restore only matching operation deltas."""

    MAX_FILES = 20_000
    MAX_FILE_BYTES = 64 * 1024 * 1024
    MAX_TOTAL_BYTES = 512 * 1024 * 1024
    # The only excluded subtree is the journal itself; all other workspace
    # files must be covered before an arbitrary shell command is allowed.
    EXCLUDED_DIRS = {CHECKPOINT_DIR_NAME}
    _OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")

    def __init__(self, workspace: Path, owner_id: str = None,
                 coordinator: MutationCoordinator = None,
                 checkpoint_enabled: bool = True):
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise JournalError("workspace must be an existing directory")
        try:
            self._workspace_fd = FileSystemUtils.open_workspace_directory(self.workspace)
            self._workspace_fd_error = ""
        except (OSError, DirectoryIdentityError) as exc:
            self._workspace_fd = None
            self._workspace_fd_error = str(exc)
        self.checkpoint_enabled = checkpoint_enabled
        self.owner_id = owner_id or uuid.uuid4().hex
        if not self._OWNER_RE.fullmatch(self.owner_id):
            raise JournalError("invalid task journal owner id")
        self.coordinator = coordinator or MutationCoordinator()
        self._journal_root: Optional[Path] = None
        self._blob_dir: Optional[Path] = None
        self._journal_fd: Optional[int] = None
        self._next_operation = 0
        self._operation_records: List[Dict[str, Any]] = []
        self._tool_invocations: Dict[str, Dict[str, Any]] = {}
        self._operations: List[JournalOperation] = []
        self._aggregate: Dict[str, Tuple[PathState, PathState]] = {}
        self._rollback_skips: List[str] = []
        self._retained_preimages: List[str] = []
        self._retained_expected: List[Tuple[str, PathState, str, str]] = []
        self._active: Dict[str, MutationTicket] = {}
        self._closed = False
        self._audit_sequence = 0
        self._audit_previous_hash = "0" * 64

    @property
    def journal_root(self) -> Optional[Path]:
        return self._journal_root

    @property
    def has_successful_mutations(self) -> bool:
        return any(record.get("status") == "committed" and record.get("changes")
                   for record in self._operation_records)

    def _ensure_ready(self) -> None:
        if self._closed:
            raise JournalError("task journal is already closed")
        if self._journal_root is not None:
            return
        checkpoint_root = self.workspace / CHECKPOINT_DIR_NAME
        if checkpoint_root.is_symlink():
            raise JournalError("checkpoint directory must not be a symlink")
        checkpoint_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            checkpoint_root.resolve(strict=True).relative_to(self.workspace)
        except (OSError, ValueError) as exc:
            raise JournalError("checkpoint directory escapes workspace") from exc
        os.chmod(checkpoint_root, 0o700)
        journals_root = checkpoint_root / "journals"
        if journals_root.is_symlink():
            raise JournalError("journal directory must not be a symlink")
        journals_root.mkdir(mode=0o700, exist_ok=True)
        os.chmod(journals_root, 0o700)
        root = journals_root / self.owner_id
        if root.is_symlink():
            raise JournalError("task journal path must not be a symlink")
        if root.exists():
            raise JournalError("task journal owner id already exists")
        root.mkdir(mode=0o700)
        try:
            root.resolve(strict=True).relative_to(journals_root.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise JournalError("task journal path escapes checkpoint directory") from exc
        blobs = root / "blobs"
        blobs.mkdir(mode=0o700)
        os.chmod(root, 0o700)
        os.chmod(blobs, 0o700)
        self._journal_root = root
        self._blob_dir = blobs
        self._journal_fd = FileSystemUtils.open_workspace_directory(root)
        self._persist_manifest()

    @staticmethod
    def _safe_rel(value: Any) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise JournalError("mutation target must be a non-empty relative path")
        requested = Path(value)
        if requested.is_absolute():
            raise JournalError("absolute mutation paths are not allowed")
        if any(part in ("..", "") for part in requested.parts):
            raise JournalError("mutation path traversal is not allowed")
        return requested

    def _target_paths(self, file_path: Any) -> List[str]:
        requested = self._safe_rel(file_path)
        target = self.workspace / requested
        current = self.workspace
        for part in requested.parts:
            current = current / part
            if current.is_symlink():
                raise JournalError(f"mutation path traverses a symlink: {requested}")
            if is_sensitive_path(current):
                raise JournalError("credential-like paths cannot be journaled or modified")
            try:
                current.resolve(strict=False).relative_to(self.workspace)
            except (OSError, ValueError) as exc:
                raise JournalError("mutation path escapes workspace") from exc
        try:
            rel_target = target.resolve(strict=False).relative_to(self.workspace)
        except (OSError, ValueError) as exc:
            raise JournalError("mutation path escapes workspace") from exc
        if target.exists() and target.is_dir():
            raise JournalError("mutation target is a directory")
        if target.exists() and FileSystemUtils.is_hard_linked_regular_file(target):
            raise JournalError("hard-linked mutation targets cannot be journaled")
        paths = [rel_target.as_posix()]
        parent = rel_target.parent
        while parent != Path("."):
            parent_path = self.workspace / parent
            if parent_path.exists():
                break
            paths.append(parent.as_posix())
            parent = parent.parent
        return paths

    def _expected_output_hashes(self, tool_name: str, args: Dict[str, Any],
                                paths: Sequence[str],
                                before: Dict[str, PathState]) -> Dict[str, str]:
        """Derive deterministic native write/edit output for rollback ownership checks."""
        if not paths:
            return {}
        target = paths[0]
        try:
            if tool_name == "write_file":
                content = args.get("content")
                if isinstance(content, str):
                    return {target: hashlib.sha256(content.encode("utf-8")).hexdigest()}
                return {}
            if tool_name != "edit_file":
                return {}
            state = before.get(target)
            search, replacement = args.get("search"), args.get("replace")
            if (state is None or state.kind != "file" or not state.backup
                    or not isinstance(search, str) or not isinstance(replacement, str)):
                return {}
            content = Path(state.backup).read_text(encoding="utf-8")
            content = content.replace("\r\n", "\n")
            search = search.replace("\r\n", "\n").strip("\n")
            replacement = replacement.replace("\r\n", "\n").strip("\n")
            if not search or content.count(search) != 1:
                return {}
            expected = content.replace(search, replacement, 1)
            return {target: hashlib.sha256(expected.encode("utf-8")).hexdigest()}
        except (OSError, UnicodeError, ValueError):
            # A missing/undecodable preimage is not permission to infer ownership.
            return {}

    def _blob_for(self, path: Path, expected_stat: os.stat_result) -> Tuple[str, str]:
        """Copy a regular file into a private content-addressed journal blob."""
        assert self._blob_dir is not None
        if expected_stat.st_size > self.MAX_FILE_BYTES:
            raise JournalError(f"file exceeds journal limit: {path}")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        temp_name = None
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "rb") as source:
                before = os.fstat(source.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_nlink > 1:
                    raise JournalError(f"unsupported file type or hard link: {path}")
                signature = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
                if signature(before) != signature(expected_stat):
                    raise JournalError(f"file changed before checkpoint copy: {path}")
                fd_tmp, temp_name = tempfile.mkstemp(prefix=".blob-", dir=self._blob_dir)
                with os.fdopen(fd_tmp, "wb") as target:
                    os.fchmod(target.fileno(), 0o600)
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > self.MAX_FILE_BYTES:
                            raise JournalError(f"file exceeds journal limit: {path}")
                        digest.update(chunk)
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                after = os.fstat(source.fileno())
                if signature(before) != signature(after) or size != before.st_size:
                    raise JournalError(f"file changed while checkpointing: {path}")
            sha = digest.hexdigest()
            blob = self._blob_dir / f"{sha}.bin"
            if blob.exists():
                os.unlink(temp_name)
                temp_name = None
            else:
                os.replace(temp_name, blob)
                temp_name = None
                os.chmod(blob, 0o600)
                # Persist the directory entry as well as the blob contents.
                # Without this, a power loss can lose the rename after fsync(blob).
                self._fsync_directory(self._blob_dir)
            return sha, str(blob)
        except JournalError:
            raise
        except Exception as exc:
            raise JournalError(f"could not checkpoint {path}: {exc}") from exc
        finally:
            if temp_name and os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        if not hasattr(os, "O_DIRECTORY"):
            return
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def _capture_one(self, rel: str, save_backup: bool = False) -> PathState:
        path = self.workspace / Path(rel)
        try:
            path.resolve(strict=False).relative_to(self.workspace)
        except (OSError, ValueError) as exc:
            raise JournalError(f"journal path escapes workspace: {rel}") from exc
        try:
            info = path.lstat()
        except FileNotFoundError:
            return PathState(kind="missing")
        except OSError as exc:
            raise JournalError(f"could not inspect {rel}: {exc}") from exc
        mode = stat.S_IMODE(info.st_mode)
        common = dict(mode=mode, device=info.st_dev, inode=info.st_ino,
                      size=info.st_size, mtime_ns=info.st_mtime_ns)
        if stat.S_ISLNK(info.st_mode):
            try:
                target = os.readlink(path)
            except OSError as exc:
                raise JournalError(f"could not inspect symlink {rel}: {exc}") from exc
            return PathState(kind="symlink", link_target=target, **common)
        if stat.S_ISDIR(info.st_mode):
            return PathState(kind="directory", **common)
        if not stat.S_ISREG(info.st_mode):
            return PathState(kind="special", **common)
        if is_sensitive_path(path) or FileSystemUtils.is_hard_linked_regular_file(path):
            raise JournalError(f"sensitive or hard-linked file cannot be journaled: {rel}")
        backup = ""
        sha = ""
        if save_backup:
            sha, backup = self._blob_for(path, info)
        else:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                with os.fdopen(fd, "rb") as source:
                    before = os.fstat(source.fileno())
                    signature = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
                    if (not stat.S_ISREG(before.st_mode) or before.st_nlink > 1
                            or signature(before) != signature(info)):
                        raise JournalError(f"file changed before fingerprinting: {rel}")
                    digest = hashlib.sha256()
                    size = 0
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > self.MAX_FILE_BYTES:
                            raise JournalError(f"file exceeds journal limit: {rel}")
                        digest.update(chunk)
                    after = os.fstat(source.fileno())
                    if signature(before) != signature(after) or size != before.st_size:
                        raise JournalError(f"file changed while fingerprinting: {rel}")
                    sha = digest.hexdigest()
            except JournalError:
                raise
            except Exception as exc:
                raise JournalError(f"could not fingerprint {rel}: {exc}") from exc
        return PathState(kind="file", sha256=sha, backup=backup, **common)

    def _capture_workspace(self, save_backups: bool) -> Dict[str, PathState]:
        states: Dict[str, PathState] = {}
        total_bytes = 0
        def onerror(error):
            raise JournalError(f"workspace checkpoint scan failed: {error}")
        for root, dirs, files in os.walk(self.workspace, topdown=True,
                                        followlinks=False, onerror=onerror):
            root_path = Path(root)
            kept_dirs = []
            for dirname in list(dirs):
                path = root_path / dirname
                if dirname in self.EXCLUDED_DIRS or is_sensitive_path(path):
                    continue
                try:
                    rel = path.relative_to(self.workspace).as_posix()
                except ValueError:
                    continue
                if path.is_symlink():
                    states[rel] = self._capture_one(rel, save_backup=False)
                    continue
                kept_dirs.append(dirname)
                states[rel] = self._capture_one(rel, save_backup=False)
            dirs[:] = kept_dirs
            for filename in files:
                path = root_path / filename
                if is_sensitive_path(path):
                    continue
                try:
                    rel = path.relative_to(self.workspace).as_posix()
                except ValueError:
                    continue
                state = self._capture_one(rel, save_backup=save_backups)
                if state.kind == "file":
                    total_bytes += state.size
                    if total_bytes > self.MAX_TOTAL_BYTES:
                        raise JournalError("workspace exceeds mutation journal size limit")
                states[rel] = state
                if len(states) > self.MAX_FILES:
                    raise JournalError("workspace exceeds mutation journal file-count limit")
        return states

    def _persist_manifest(self) -> None:
        if self._journal_root is None:
            return
        payload = {
            "version": 2,
            "owner_id": self.owner_id,
            "workspace": str(self.workspace),
            "operations": self._operation_records,
            "tool_invocations": self._tool_invocations,
            "rollback_skips": self._rollback_skips,
            "retained_preimages": self._retained_preimages,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False).encode("utf-8")
        payload_hash = hashlib.sha256(canonical).hexdigest()
        sequence = self._audit_sequence + 1
        chain_body = {"sequence": sequence, "previous_hash": self._audit_previous_hash,
                      "payload_hash": payload_hash}
        chain_hash = hashlib.sha256(json.dumps(
            chain_body, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        chain_entry = {**chain_body, "entry_hash": chain_hash}
        chain_path = self._journal_root / "audit-chain.jsonl"
        if chain_path.is_symlink():
            raise JournalError("audit chain path must not be a symlink")
        try:
            chain_fd = os.open(chain_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                record = memoryview((json.dumps(chain_entry, sort_keys=True) + "\n").encode("utf-8"))
                while record:
                    written = os.write(chain_fd, record)
                    if written <= 0:
                        raise OSError("short write while appending audit chain")
                    record = record[written:]
                os.fsync(chain_fd)
            finally:
                os.close(chain_fd)
        except OSError as exc:
            raise JournalError(f"could not persist journal audit chain: {exc}") from exc
        payload["integrity"] = {"sequence": sequence, "payload_hash": payload_hash,
                                "chain_hash": chain_hash}
        target = self._journal_root / "manifest.json"
        fd, temp = tempfile.mkstemp(prefix=".manifest-", dir=self._journal_root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, target)
            os.chmod(target, 0o600)
            if hasattr(os, "O_DIRECTORY"):
                dir_fd = os.open(self._journal_root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            self._audit_sequence = sequence
            self._audit_previous_hash = chain_hash
        except Exception as exc:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise JournalError(f"could not persist task journal: {exc}") from exc

    def _state_map_manifest(self, states: Dict[str, PathState]) -> Dict[str, Any]:
        assert self._journal_root is not None
        return {path: state.manifest(self._journal_root)
                for path, state in sorted(states.items())}

    def claim_tool_invocation(self, call_id: str, tool_name: str,
                              args: Dict[str, Any]) -> Dict[str, Any]:
        """Durably claim a provider tool-call ID before a mutating invocation."""
        if not isinstance(call_id, str) or not call_id.strip():
            raise JournalError("mutating tool call is missing a stable call ID")
        self._ensure_ready()
        key = hashlib.sha256(call_id.encode("utf-8", errors="replace")).hexdigest()
        args_blob = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
        args_hash = hashlib.sha256(args_blob.encode("utf-8")).hexdigest()
        existing = self._tool_invocations.get(key)
        if existing is not None:
            if existing.get("tool_name") != tool_name or existing.get("args_hash") != args_hash:
                raise JournalError("tool-call ID was reused with different arguments")
            return {"claimed": False, "status": existing.get("status", "uncertain")}
        self._tool_invocations[key] = {
            "tool_name": str(tool_name)[:120], "args_hash": args_hash,
            "status": "in_flight",
        }
        self._persist_manifest()
        return {"claimed": True, "status": "in_flight"}

    def finish_tool_invocation(self, call_id: str, status: str) -> None:
        if not isinstance(call_id, str) or not call_id.strip():
            raise JournalError("mutating tool call is missing a stable call ID")
        key = hashlib.sha256(call_id.encode("utf-8", errors="replace")).hexdigest()
        record = self._tool_invocations.get(key)
        if record is None:
            raise JournalError("tool invocation was not claimed")
        record["status"] = "completed" if status == "ok" else "failed"
        self._persist_manifest()

    def begin_operation(self, tool_name: str, args: Dict[str, Any]) -> MutationTicket:
        if not self.checkpoint_enabled:
            raise JournalError("mutations are disabled because checkpointing is disabled")
        self._ensure_ready()
        self._next_operation += 1
        operation_id = f"op-{self._next_operation:05d}"
        is_shell = tool_name == "run_command"
        scope = "workspace" if is_shell else "paths"
        claimed_paths: List[str] = []
        workspace_claimed = False
        parent_states_before: Dict[str, ParentDirectoryState] = {}
        try:
            if is_shell:
                self.coordinator.claim_workspace(self.owner_id)
                workspace_claimed = True
                before = self._capture_workspace(save_backups=True)
                paths = sorted(before)
            elif tool_name in ("write_file", "edit_file"):
                paths = self._target_paths(args.get("file_path", ""))
                self.coordinator.claim_paths(self.owner_id, paths)
                claimed_paths = paths
                if self._workspace_fd is None:
                    raise JournalError(
                        "native mutations require a pinned workspace dirfd; "
                        f"dirfd setup failed: {self._workspace_fd_error}")
                for path in paths:
                    parent = PurePosixPath(path).parent.as_posix()
                    parent_states_before[path] = FileSystemUtils.capture_parent_directory_state(
                        self._workspace_fd, self.workspace, parent)
                before = {path: self._capture_one(path, save_backup=True)
                          for path in paths}
                for state in parent_states_before.values():
                    FileSystemUtils.verify_parent_directory_state(
                        self._workspace_fd, self.workspace, state)
            else:
                raise JournalError(f"unsupported mutating tool: {tool_name}")
            expected_outputs = self._expected_output_hashes(
                tool_name, args, paths, before) if scope == "paths" else {}
            ticket = MutationTicket(
                operation_id=operation_id, tool_name=tool_name,
                owner_id=self.owner_id, scope=scope, paths=paths,
                before=before, claimed_paths=claimed_paths,
                workspace_claimed=workspace_claimed,
                expected_outputs=expected_outputs,
                parent_states_before=parent_states_before,
            )
            self._active[operation_id] = ticket
            record = {
                "operation_id": operation_id,
                "tool_name": tool_name,
                "status": "checkpointed",
                "scope": scope,
                "expected_outputs": dict(expected_outputs),
                "before": self._state_map_manifest(before),
                "parent_identities_before": {
                    path: state.manifest()
                    for path, state in sorted(parent_states_before.items())
                },
                "after": {},
                "changes": [],
            }
            self._operation_records.append(record)
            self._persist_manifest()
            return ticket
        except Exception as exc:
            if claimed_paths:
                self.coordinator.release_paths(self.owner_id, claimed_paths)
            if workspace_claimed:
                self.coordinator.release_workspace(self.owner_id)
            if isinstance(exc, JournalError):
                raise
            raise JournalError(f"could not establish task checkpoint: {exc}") from exc

    def _capture_ticket_scope(self, ticket: MutationTicket,
                              save_backups: bool = False) -> Dict[str, PathState]:
        if ticket.scope == "workspace":
            return self._capture_workspace(save_backups=save_backups)
        return {path: self._capture_one(path, save_backup=save_backups)
                for path in ticket.paths}

    def finish_operation(self, ticket: MutationTicket) -> JournalOperation:
        if ticket.operation_id not in self._active or ticket.ended:
            raise JournalError("mutation ticket is not active")
        try:
            if ticket.parent_anchor is not None:
                try:
                    ticket.parent_anchor.verify_live()
                except Exception as exc:
                    self._record_parent_conflict(ticket, str(exc))
            after = self._capture_ticket_scope(ticket, save_backups=False)
            if ticket.scope == "paths":
                try:
                    ticket.parent_states_after = self._capture_parent_states(ticket)
                except Exception as exc:
                    self._record_parent_conflict(ticket, f"post-operation parent scan failed: {exc}")
                if ticket.parent_anchor is not None:
                    primary = ticket.paths[0] if ticket.paths else ""
                    observed = ticket.parent_states_after.get(primary)
                    if observed is not None and observed != ticket.parent_anchor.state:
                        self._record_parent_conflict(
                            ticket, "workspace path no longer resolves to the pinned parent inode")
                    try:
                        ticket.parent_anchor.verify_live()
                    except Exception as exc:
                        self._record_parent_conflict(ticket, str(exc))
            for detail in self._check_retained_preimages(ticket.operation_id):
                if detail not in ticket.precommit_conflicts:
                    ticket.precommit_conflicts.append(detail)
            paths = sorted(set(ticket.before) | set(after))
            changes = []
            ambiguous_paths: Set[str] = set()
            provenance: Dict[str, str] = {}
            for path in paths:
                before_state = ticket.before.get(path, PathState("missing"))
                after_state = after.get(path, PathState("missing"))
                if not before_state.equivalent(after_state):
                    changes.append(JournalChange(path, before_state, after_state))
                    expected_hash = ticket.expected_outputs.get(path)
                    if ticket.scope == "workspace":
                        ambiguous_paths.add(path)
                        provenance[path] = "ambiguous-workspace-writer"
                    elif ticket.precommit_conflicts:
                        ambiguous_paths.add(path)
                        provenance[path] = "precommit-conflict"
                    elif (expected_hash and after_state.kind == "file"
                          and after_state.sha256 == expected_hash
                          and (before_state.kind == "missing"
                               or before_state.inode != after_state.inode)):
                        provenance[path] = "expected-output-matched"
                    else:
                        ambiguous_paths.add(path)
                        provenance[path] = "ambiguous-native-output"
                    if path in self._aggregate:
                        first_before, _ = self._aggregate[path]
                        self._aggregate[path] = (first_before, after_state)
                    else:
                        self._aggregate[path] = (before_state, after_state)
            if ticket.precommit_conflicts and ticket.paths:
                ambiguous_paths.update(ticket.paths)
                for path in ticket.paths:
                    provenance[path] = "precommit-conflict"
            operation = JournalOperation(
                ticket=ticket, changes=changes, ambiguous_paths=ambiguous_paths)
            self._operations.append(operation)
            ticket.ended = True
            self._active.pop(ticket.operation_id, None)
            record = self._record_for(ticket.operation_id)
            record["status"] = "finished"
            record["after"] = self._state_map_manifest(after)
            record["parent_identities_after"] = {
                path: state.manifest()
                for path, state in sorted(ticket.parent_states_after.items())
            }
            record["ambiguous_paths"] = sorted(ambiguous_paths)
            record["provenance"] = provenance
            record["precommit_conflicts"] = list(ticket.precommit_conflicts)
            record["changes"] = [
                {"path": change.path,
                 "before": change.before.manifest(self._journal_root),
                 "after": change.after.manifest(self._journal_root),
                 "provenance": provenance.get(change.path, "ambiguous")}
                for change in changes
            ]
            self._persist_manifest()
            return operation
        except Exception as exc:
            ticket.ended = True
            self._active.pop(ticket.operation_id, None)
            record = self._record_for(ticket.operation_id)
            record["status"] = "uncertain"
            record["error"] = str(exc)
            conflict = (
                f"CONFLICT/UNCERTAIN: operation {ticket.operation_id} post-scan failed; "
                f"workspace state may have changed and was preserved without rollback ({exc})")
            self._rollback_skips.append(conflict)
            record["conflict"] = conflict
            try:
                self._persist_manifest()
            except Exception:
                pass
            operation = JournalOperation(ticket=ticket, changes=[], capture_error=str(exc))
            self._operations.append(operation)
            return operation

    def _record_for(self, operation_id: str) -> Dict[str, Any]:
        for record in self._operation_records:
            if record.get("operation_id") == operation_id:
                return record
        raise JournalError(f"missing journal record for {operation_id}")

    def _current_state(self, rel: str) -> PathState:
        return self._capture_one(rel, save_backup=False)

    def _record_parent_conflict(self, ticket: MutationTicket, detail: str) -> str:
        primary = ticket.paths[0] if ticket.paths else ticket.operation_id
        message = (f"{primary}: parent directory identity conflict/uncertain; {detail}")
        if message not in ticket.precommit_conflicts:
            ticket.precommit_conflicts.append(message)
        return message

    def _capture_parent_states(self, ticket: MutationTicket) -> Dict[str, ParentDirectoryState]:
        if self._workspace_fd is None:
            raise JournalError("pinned workspace directory handle is unavailable")
        result = {}
        for path in ticket.paths:
            parent = PurePosixPath(path).parent.as_posix()
            result[path] = FileSystemUtils.capture_parent_directory_state(
                self._workspace_fd, self.workspace, parent)
        return result

    def open_parent_anchor(self, ticket: MutationTicket) -> ParentDirectoryAnchor:
        primary = ticket.paths[0] if ticket.paths else ""
        try:
            if (ticket.operation_id not in self._active or ticket.ended
                    or ticket.tool_name not in ("write_file", "edit_file")
                    or not primary or self._workspace_fd is None):
                raise JournalError("native parent identity cannot be pinned for this ticket")
            state = ticket.parent_states_before.get(primary)
            if state is None:
                raise JournalError("native parent identity snapshot is unavailable")
            anchor = FileSystemUtils.open_parent_anchor(
                self._workspace_fd, self.workspace, state, primary)
            ticket.parent_anchor = anchor
            return anchor
        except Exception as exc:
            self._record_parent_conflict(ticket, str(exc))
            try:
                record = self._record_for(ticket.operation_id)
                record["precommit_conflicts"] = list(ticket.precommit_conflicts)
                self._persist_manifest()
            except Exception:
                pass
            if isinstance(exc, JournalError):
                raise
            raise JournalError(f"parent directory identity could not be pinned: {exc}") from exc

    def _capture_one_at(self, anchor: ParentDirectoryAnchor,
                        name: str, rel: str) -> PathState:
        if is_sensitive_path(self.workspace / rel):
            raise JournalError(f"sensitive path cannot be captured: {rel}")
        try:
            info = anchor.stat_entry(name)
        except OSError as exc:
            raise JournalError(f"could not inspect pinned target {rel}: {exc}") from exc
        if info is None:
            return PathState(kind="missing")
        mode = stat.S_IMODE(info.st_mode)
        common = dict(mode=mode, device=info.st_dev, inode=info.st_ino,
                      size=info.st_size, mtime_ns=info.st_mtime_ns)
        if stat.S_ISLNK(info.st_mode):
            try:
                target = os.readlink(name, dir_fd=anchor.fd)
            except OSError as exc:
                raise JournalError(f"could not inspect pinned symlink {rel}: {exc}") from exc
            return PathState(kind="symlink", link_target=target, **common)
        if stat.S_ISDIR(info.st_mode):
            return PathState(kind="directory", **common)
        if not stat.S_ISREG(info.st_mode):
            return PathState(kind="special", **common)
        if info.st_size > self.MAX_FILE_BYTES:
            raise JournalError(f"pinned file exceeds journal limit: {rel}")
        try:
            data, opened = anchor.read_regular_file(name)
        except Exception as exc:
            raise JournalError(f"could not fingerprint pinned file {rel}: {exc}") from exc
        return PathState(kind="file", sha256=hashlib.sha256(data).hexdigest(),
                         mode=stat.S_IMODE(opened.st_mode), size=len(data),
                         device=opened.st_dev, inode=opened.st_ino,
                         mtime_ns=opened.st_mtime_ns)

    def assert_precommit_owned(self, ticket: MutationTicket,
                               candidate_name: str,
                               anchor: ParentDirectoryAnchor) -> None:
        """Require the staged target entry to still match the native operation's pre-state."""
        primary = ticket.paths[0] if ticket.paths else ""
        detail = ""
        try:
            if (ticket.operation_id not in self._active or ticket.ended
                    or ticket.tool_name not in ("write_file", "edit_file")
                    or primary not in ticket.expected_outputs):
                raise JournalError("native output ownership could not be established")
            if anchor.target_relative != primary:
                raise DirectoryIdentityError("parent directory anchor target does not match ticket")
            anchor.verify_live()
            expected_before = ticket.before.get(primary)
            current = self._capture_one_at(anchor, Path(candidate_name).name, primary)
            if expected_before is None or not current.matches(expected_before):
                raise JournalError("target identity/fingerprint changed before commit")
            return
        except Exception as exc:
            if isinstance(exc, DirectoryIdentityError) or "parent directory identity" in str(exc).lower():
                detail = self._record_parent_conflict(ticket, str(exc))
            else:
                detail = (f"{primary or ticket.operation_id}: native commit conflict/ambiguous; "
                          f"expected pre-state no longer matches ({exc})")
                if detail not in ticket.precommit_conflicts:
                    ticket.precommit_conflicts.append(detail)
            try:
                record = self._record_for(ticket.operation_id)
                record["precommit_conflicts"] = list(ticket.precommit_conflicts)
                self._persist_manifest()
            except Exception:
                pass
            if isinstance(exc, JournalError):
                raise JournalError(detail) from exc
            raise JournalError(detail) from exc

    def retain_displaced_preimage(self, ticket: MutationTicket,
                                  quarantine_name: str,
                                  anchor: ParentDirectoryAnchor) -> Path:
        """Keep the displaced inode privately named so late open-handle writes survive."""
        primary = ticket.paths[0] if ticket.paths else ""
        quarantine_name = Path(quarantine_name).name
        try:
            if (ticket.operation_id not in self._active or ticket.ended
                    or not primary or self._journal_root is None
                    or self._journal_fd is None):
                raise JournalError("native preimage retention has no active journal ticket")
            expected_before = ticket.before.get(primary)
            if expected_before is None or expected_before.kind != "file":
                raise JournalError("native preimage retention requires a recorded existing file")
            anchor.verify_live()
            if not self._capture_one_at(anchor, quarantine_name, primary).matches(expected_before):
                raise JournalError("displaced inode changed before it could be retained")

            try:
                os.mkdir("retained-inodes", mode=0o700, dir_fd=self._journal_fd)
            except FileExistsError:
                pass
            retained_fd = os.open("retained-inodes",
                                  FileSystemUtils._directory_open_flags(),
                                  dir_fd=self._journal_fd)
            name = (f"{ticket.operation_id}-"
                    f"{hashlib.sha256(primary.encode('utf-8')).hexdigest()[:12]}.inode")
            retained = self._journal_root / "retained-inodes" / name
            # Link first, then unlink the quarantine name. A writer holding an
            # open descriptor to the displaced inode can no longer lose its
            # late write when the temporary quarantine name is removed.
            try:
                os.link(quarantine_name, name, src_dir_fd=anchor.fd,
                        dst_dir_fd=retained_fd, follow_symlinks=False)
                retained_rel = retained.relative_to(self.workspace).as_posix()
                quarantine_info = anchor.stat_entry(quarantine_name)
                retained_info = os.stat(name, dir_fd=retained_fd,
                                        follow_symlinks=False)
                if (quarantine_info is None
                        or (quarantine_info.st_dev, quarantine_info.st_ino)
                        != (retained_info.st_dev, retained_info.st_ino)
                        or (retained_info.st_dev, retained_info.st_ino)
                        != (expected_before.device, expected_before.inode)):
                    raise JournalError("retained entry is not the checkpointed original inode")
            finally:
                os.close(retained_fd)

            ticket.retained_preimages.append(retained_rel)
            self._retained_preimages.append(f"{primary} -> {retained_rel}")
            self._retained_expected.append(
                (retained_rel, expected_before, primary, ticket.operation_id))
            record = self._record_for(ticket.operation_id)
            record["retained_preimages"] = [
                {"source_path": primary, "path": path,
                 "expected_before": expected_before.manifest(self._journal_root),
                 "purpose": "preserve late writes through an open handle to the displaced inode"}
                for path in ticket.retained_preimages
            ]
            self._persist_manifest()
            anchor.unlink(quarantine_name)
            anchor.verify_live()
            for detail in self._check_retained_preimages(ticket.operation_id):
                ticket.precommit_conflicts.append(detail)
            if ticket.precommit_conflicts:
                record["precommit_conflicts"] = list(ticket.precommit_conflicts)
                self._persist_manifest()
            return retained
        except Exception as exc:
            detail = (f"{primary or ticket.operation_id}: native commit conflict/ambiguous; "
                      f"displaced inode was not safely retired ({exc})")
            if detail not in ticket.precommit_conflicts:
                ticket.precommit_conflicts.append(detail)
            try:
                record = self._record_for(ticket.operation_id)
                record["precommit_conflicts"] = list(ticket.precommit_conflicts)
                self._persist_manifest()
            except Exception:
                pass
            if isinstance(exc, JournalError):
                raise JournalError(detail) from exc
            raise JournalError(detail) from exc

    def _check_retained_preimages(self, operation_id: str = None) -> List[str]:
        conflicts: List[str] = []
        for rel, expected, source, owner_operation in self._retained_expected:
            if operation_id is not None and owner_operation != operation_id:
                continue
            try:
                current = self._current_state(rel)
                if current.matches(expected):
                    continue
                reason = "late write changed the retained original inode"
            except Exception as exc:
                reason = f"retained original inode could not be checked ({exc})"
            detail = f"{source}: {reason}; preserved at {rel}"
            message = "CONFLICT/AMBIGUOUS: " + detail
            if message not in self._rollback_skips:
                self._rollback_skips.append(message)
            try:
                record = self._record_for(owner_operation)
                record["retained_preimage_conflict"] = detail
                self._persist_manifest()
            except Exception:
                pass
            conflicts.append(detail)
        return conflicts

    def _validate_restore_path(self, rel: str) -> Path:
        candidate = PurePosixPath(rel)
        if candidate.is_absolute() or any(part in ("", ".", "..") for part in candidate.parts):
            raise JournalError(f"unsafe rollback path: {rel}")
        current = self.workspace
        for part in candidate.parts:
            current = current / part
            if current.is_symlink():
                raise JournalError(f"rollback path traverses symlink: {rel}")
            try:
                current.resolve(strict=False).relative_to(self.workspace)
            except (OSError, ValueError) as exc:
                raise JournalError(f"rollback path escapes workspace: {rel}") from exc
        if is_sensitive_path(current):
            raise JournalError(f"sensitive path cannot be restored: {rel}")
        return current

    def _quarantine_current_file(self, rel: str, anchor: ParentDirectoryAnchor,
                                 expected: PathState) -> Tuple[str, str]:
        """Move one file using the pinned parent fd, then verify the moved inode."""
        fd, name = anchor.create_temp_file(".zx-rollback-current-")
        os.close(fd)
        try:
            anchor.verify_live()
            anchor.rename(anchor.target_name, name)
        except Exception:
            if anchor.stat_entry(anchor.target_name) is not None:
                try:
                    anchor.unlink(name)
                except OSError:
                    pass
            raise
        parent = PurePosixPath(rel).parent
        quarantine_rel = (parent / name).as_posix() if parent.as_posix() != "." else name
        moved_state = self._capture_one_at(anchor, name, rel)
        if not moved_state.matches(expected):
            restored = False
            try:
                if anchor.stat_entry(anchor.target_name) is None:
                    anchor.link(name, anchor.target_name)
                    anchor.unlink(name)
                    restored = True
            except OSError:
                pass
            detail = ("external state restored to its path" if restored
                      else f"external state preserved at {quarantine_rel}")
            raise JournalError(f"path changed during rollback; {detail}")
        return name, quarantine_rel

    def _restore_change(self, change: JournalChange,
                        anchor: ParentDirectoryAnchor) -> None:
        before, after = change.before, change.after
        target_name = anchor.target_name
        anchor.verify_live()
        if not self._capture_one_at(anchor, target_name, change.path).matches(after):
            raise JournalError("target changed immediately before restore; preserving current state")
        if before.kind in ("symlink", "special") or after.kind in ("symlink", "special"):
            raise JournalError("symlink/special-file changes are not automatically restored")

        if before.kind == "missing":
            if after.kind == "file":
                quarantine, quarantine_rel = self._quarantine_current_file(
                    change.path, anchor, after)
                if not self._capture_one_at(anchor, quarantine, change.path).matches(after):
                    raise JournalError(f"created file changed during removal; preserved at {quarantine_rel}")
                anchor.unlink(quarantine)
                anchor.verify_live()
            elif after.kind == "directory":
                anchor.rmdir(target_name)
                anchor.verify_live()
            return

        if before.kind == "directory":
            if after.kind == "missing":
                anchor.mkdir(target_name, mode=before.mode or 0o700)
            elif after.kind == "directory" and before.mode != after.mode:
                fd = os.open(target_name, FileSystemUtils._directory_open_flags(),
                             dir_fd=anchor.fd)
                try:
                    info = os.fstat(fd)
                    if (info.st_dev, info.st_ino) != (after.device, after.inode):
                        raise JournalError("directory identity changed before mode restore")
                    os.fchmod(fd, before.mode)
                finally:
                    os.close(fd)
            elif after.kind == "file":
                quarantine, quarantine_rel = self._quarantine_current_file(
                    change.path, anchor, after)
                anchor.mkdir(target_name, mode=before.mode or 0o700)
                if not self._capture_one_at(anchor, quarantine, change.path).matches(after):
                    raise JournalError(f"file changed during directory restore; preserved at {quarantine_rel}")
                anchor.unlink(quarantine)
            anchor.verify_live()
            return

        if before.kind != "file":
            raise JournalError(f"unsupported pre-mutation state for {change.path}")
        if not before.backup or self._journal_fd is None:
            raise JournalError("pre-mutation file backup is unavailable")
        assert self._journal_root is not None
        backup = Path(before.backup)
        try:
            backup_rel = backup.relative_to(self._journal_root)
        except ValueError as exc:
            raise JournalError("pre-mutation backup escaped the private journal") from exc
        if backup_rel.parts != ("blobs", f"{before.sha256}.bin"):
            raise JournalError("pre-mutation backup path is not the expected content-addressed blob")

        blobs_fd = os.open("blobs", FileSystemUtils._directory_open_flags(),
                           dir_fd=self._journal_fd)
        source_fd = None
        temp_fd = None
        temp_name = None
        quarantine = None
        quarantine_rel = ""
        try:
            source_fd = os.open(backup_rel.name, os.O_RDONLY | os.O_NOFOLLOW,
                                dir_fd=blobs_fd)
            source_info = os.fstat(source_fd)
            if not stat.S_ISREG(source_info.st_mode) or source_info.st_nlink != 1:
                raise JournalError("pre-mutation backup is not a private regular file")
            temp_fd, temp_name = anchor.create_temp_file(".zx-restore-")
            digest = hashlib.sha256()
            total = 0
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > self.MAX_FILE_BYTES:
                    raise JournalError("pre-mutation backup exceeds restore limit")
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(temp_fd, view)
                    view = view[written:]
            if digest.hexdigest() != before.sha256 or total != before.size:
                raise JournalError("pre-mutation backup content does not match its recorded fingerprint")
            os.fchmod(temp_fd, before.mode)
            os.fsync(temp_fd)
            os.close(temp_fd)
            temp_fd = None

            anchor.verify_live()
            if not self._capture_one_at(anchor, target_name, change.path).matches(after):
                raise JournalError("target changed immediately before restore commit; preserving current state")
            if after.kind == "directory":
                anchor.rmdir(target_name)
            elif after.kind == "file":
                quarantine, quarantine_rel = self._quarantine_current_file(
                    change.path, anchor, after)
            elif after.kind != "missing":
                raise JournalError(f"unsupported post-mutation state for {change.path}")
            try:
                anchor.verify_live()
                anchor.link(temp_name, target_name)
            except OSError as exc:
                if quarantine is not None and anchor.stat_entry(target_name) is None:
                    try:
                        anchor.link(quarantine, target_name)
                        anchor.unlink(quarantine)
                        quarantine = None
                    except OSError:
                        pass
                raise JournalError(f"no-overwrite restore install failed; current state preserved: {exc}") from exc
            anchor.unlink(temp_name)
            temp_name = None
            anchor.verify_live()
            restored = self._capture_one_at(anchor, target_name, change.path)
            if (restored.kind != "file" or restored.sha256 != before.sha256
                    or restored.mode != before.mode or restored.size != before.size):
                raise JournalError("restored file did not match the recorded preimage")
            if quarantine is not None:
                if not self._capture_one_at(anchor, quarantine, change.path).matches(after):
                    raise JournalError(
                        f"displaced file changed during restore; preserved at {quarantine_rel}")
                anchor.unlink(quarantine)
                quarantine = None
            anchor.verify_live()
        finally:
            if source_fd is not None:
                os.close(source_fd)
            os.close(blobs_fd)
            if temp_fd is not None:
                os.close(temp_fd)
            if temp_name is not None:
                try:
                    anchor.unlink(temp_name)
                except OSError:
                    pass

    def rollback_failed_operation(self, operation: JournalOperation) -> RollbackReport:
        report = RollbackReport()
        ticket = operation.ticket
        # Write-ahead rollback intent is mandatory: after a process crash, recovery
        # must distinguish "rollback started" from a fully finished mutation.
        record = self._record_for(ticket.operation_id)
        record["status"] = "rollback_in_progress"
        record["rollback"] = {
            "status": "in_progress",
            "planned_paths": [change.path for change in operation.changes],
        }
        try:
            self._persist_manifest()
        except Exception as exc:
            message = (f"CONFLICT/UNCERTAIN: rollback intent could not be persisted; "
                       f"no automatic restore was attempted ({exc})")
            self._rollback_skips.append(message)
            report.skipped.append(message)
            self._release_ticket(ticket)
            return report
        if operation.capture_error:
            report.skipped.append(
                f"CONFLICT/UNCERTAIN: operation {ticket.operation_id} post-state could not be verified; "
                "workspace changes may exist and were preserved without rollback")
            self._rollback_skips.extend(report.skipped)
            self._release_ticket(ticket)
            return report
        for detail in ticket.precommit_conflicts:
            report.skipped.append(f"CONFLICT/AMBIGUOUS: {detail}")
        # Added paths are removed deepest-first; pre-existing paths are restored
        # shallowest-first so deleted directories exist before their files.
        ordered = sorted(
            operation.changes,
            key=lambda change: (change.before.kind != "missing",
                                -len(PurePosixPath(change.path).parts)
                                if change.before.kind == "missing"
                                else len(PurePosixPath(change.path).parts)),
        )
        for change in ordered:
            if change.path in operation.ambiguous_paths:
                report.skipped.append(
                    f"{change.path}: CONFLICT/AMBIGUOUS writer provenance; "
                    "preserved without automatic rollback")
                continue
            anchor = None
            try:
                parent_state = ticket.parent_states_after.get(change.path)
                if parent_state is None or self._workspace_fd is None:
                    raise DirectoryIdentityError(
                        "parent directory identity post-state is unavailable")
                anchor = FileSystemUtils.open_parent_anchor(
                    self._workspace_fd, self.workspace, parent_state,
                    change.path)
                anchor.verify_live()
                current = self._capture_one_at(anchor, anchor.target_name, change.path)
                if not current.matches(change.after):
                    report.skipped.append(
                        f"{change.path}: changed since operation; preserved")
                    continue
                self._restore_change(change, anchor)
                anchor.verify_live()
                report.restored.append(change.path)
                after_restore = self._capture_one_at(
                    anchor, anchor.target_name, change.path)
                if change.path in self._aggregate:
                    first_before, _ = self._aggregate[change.path]
                    self._aggregate[change.path] = (first_before, after_restore)
            except Exception as exc:
                report.skipped.append(f"{change.path}: restore skipped ({exc})")
            finally:
                if anchor is not None:
                    anchor.close()
        self._rollback_skips.extend(report.skipped)
        record = self._record_for(ticket.operation_id)
        if any("AMBIGUOUS" in item or "CONFLICT" in item
               or "parent directory identity" in item.lower()
               for item in report.skipped):
            record["status"] = "rollback_conflict"
        else:
            record["status"] = "rolled_back" if not report.skipped else "rollback_partial"
        record["rollback"] = {"restored": report.restored, "skipped": report.skipped}
        try:
            self._persist_manifest()
        except Exception:
            self._rollback_skips.append("journal manifest could not record rollback outcome")
        self._release_ticket(ticket)
        return report

    def commit_operation(self, operation: JournalOperation) -> None:
        ticket = operation.ticket
        record = self._record_for(ticket.operation_id)
        if operation.capture_error:
            record["status"] = "uncertain"
            record["error"] = operation.capture_error
        else:
            record["status"] = "committed"
        try:
            self._persist_manifest()
        except Exception as exc:
            self._rollback_skips.append(f"journal manifest update failed: {exc}")
        self._release_ticket(ticket)

    def _release_ticket(self, ticket: MutationTicket) -> None:
        if ticket.parent_anchor is not None:
            ticket.parent_anchor.close()
            ticket.parent_anchor = None
        if ticket.claimed_paths:
            self.coordinator.release_paths(ticket.owner_id, ticket.claimed_paths)
            ticket.claimed_paths.clear()
        if ticket.workspace_claimed:
            self.coordinator.release_workspace(ticket.owner_id)
            ticket.workspace_claimed = False

    def abort_operation(self, ticket: MutationTicket) -> None:
        self._active.pop(ticket.operation_id, None)
        try:
            record = self._record_for(ticket.operation_id)
            record["status"] = (
                "aborted_with_conflict" if ticket.precommit_conflicts
                else "aborted_before_mutation")
            record["precommit_conflicts"] = list(ticket.precommit_conflicts)
            self._persist_manifest()
        except Exception:
            pass
        for detail in ticket.precommit_conflicts:
            message = f"CONFLICT/AMBIGUOUS: {detail}"
            if message not in self._rollback_skips:
                self._rollback_skips.append(message)
        self._release_ticket(ticket)

    def _content(self, state: PathState, rel: str) -> Optional[str]:
        try:
            if state.kind == "missing":
                return None
            if state.kind == "file" and state.backup:
                return Path(state.backup).read_text(encoding="utf-8")
            if state.kind == "file":
                path = self.workspace / rel
                if path.is_file() and not path.is_symlink() and path.stat().st_size <= 100_000:
                    return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None
        return None

    def review_context(self, max_chars: int = 30000) -> str:
        """Return a bounded, credential-aware textual diff for independent review.

        Binary, oversized, unreadable, sensitive, symlinked and out-of-workspace
        paths are represented by metadata only. This is advisory review input,
        not a replacement for a full VCS diff.
        """
        chunks: List[str] = []
        remaining = max(1000, int(max_chars))
        for rel, (before, after) in sorted(self._aggregate.items()):
            if before.equivalent(after):
                continue
            candidate = self.workspace / rel
            try:
                resolved = candidate.resolve(strict=False)
                resolved.relative_to(self.workspace.resolve())
                if is_sensitive_path(candidate) or candidate.is_symlink():
                    chunks.append(f"\n### {rel}\n[content omitted: sensitive or symlinked path]")
                    continue
            except (OSError, ValueError):
                chunks.append(f"\n### {rel}\n[content omitted: unsafe path]")
                continue
            old_text = self._content(before, rel)
            new_text = self._content(after, rel)
            if ((old_text is not None and len(old_text) > 100_000)
                    or (new_text is not None and len(new_text) > 100_000)):
                chunks.append(f"\n### {rel}\n[content omitted: file exceeds 100 KB]")
                continue
            if old_text is None and new_text is None:
                chunks.append(f"\n### {rel}\n[content unavailable or binary]")
                continue
            old_lines = (old_text or "").splitlines(keepends=True)
            new_lines = (new_text or "").splitlines(keepends=True)
            diff = "".join(difflib.unified_diff(
                old_lines, new_lines, fromfile=f"before/{rel}", tofile=f"after/{rel}"))
            if not diff:
                diff = "[No textual diff available; metadata indicates a change.]"
            section = f"\n### {rel}\n{diff}"
            if len(section) > remaining:
                section = section[:max(0, remaining - 40)] + "\n[review diff truncated]"
            chunks.append(section)
            remaining -= len(section)
            if remaining <= 0:
                break
        return "".join(chunks) or "(no reviewable textual changes)"

    def summary(self) -> str:
        changes = [(path, before, after)
                   for path, (before, after) in sorted(self._aggregate.items())
                   if not before.equivalent(after)]
        if not changes and not self._rollback_skips and not self._retained_preimages:
            return ""
        added = sum(before.kind == "missing" and after.kind != "missing"
                    for _, before, after in changes)
        deleted = sum(before.kind != "missing" and after.kind == "missing"
                      for _, before, after in changes)
        modified = len(changes) - added - deleted
        uncertain = any("CONFLICT/UNCERTAIN" in item for item in self._rollback_skips)
        if not changes and uncertain:
            lines = ["Workspace state is CONFLICT/UNCERTAIN; file/path delta could not be determined."]
        else:
            lines = [f"Workspace diff: {len(changes)} file/path change(s) "
                     f"(+{added} added, ~{modified} modified, -{deleted} deleted)."]
        for path, before, after in changes[:12]:
            marker = "+" if before.kind == "missing" else "-" if after.kind == "missing" else "~"
            lines.append(f"{marker} {path}")
        if len(changes) > 12:
            lines.append(f"… and {len(changes) - 12} more")
        if self._journal_root is not None:
            try:
                undo = self._journal_root.relative_to(self.workspace)
                lines.append(f"Manual undo material: {undo}/manifest.json and {undo}/blobs/")
                lines.append("To undo a file, use its matching pre-mutation `before.backup` blob in the manifest.")
            except ValueError:
                pass
        if self._retained_preimages:
            retained = "; ".join(self._retained_preimages[:5])
            if len(self._retained_preimages) > 5:
                retained += f"; … and {len(self._retained_preimages) - 5} more"
            lines.append("Original native inodes retained for late open-handle writes: " + retained)
            lines.append(
                "Retained-inode cleanup is manual: first stop processes that may hold old "
                "file handles, review/archive any late-write data and the manifest, then "
                "remove only the selected retained entries. Journal close is not proof "
                "that no open handles remain; no age-based automatic deletion is performed.")
        if self._rollback_skips:
            lines.append("Preserved instead of restoring: " + "; ".join(self._rollback_skips[:5]))
        return "\n".join(lines)

    def close(self) -> str:
        if self._closed:
            return self.summary()
        for ticket in list(self._active.values()):
            self.abort_operation(ticket)
        self._check_retained_preimages()
        self.coordinator.release_owner(self.owner_id)
        try:
            self._persist_manifest()
        except Exception as exc:
            self._rollback_skips.append(f"final journal write failed: {exc}")
        self._closed = True
        for attribute in ("_journal_fd", "_workspace_fd"):
            fd = getattr(self, attribute, None)
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(self, attribute, None)
        return self.summary()
