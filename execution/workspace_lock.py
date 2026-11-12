"""Process-shared advisory lock for a single ZX Code workspace (POSIX)."""
from __future__ import annotations

import fcntl
import json
import os
import socket
import time
from pathlib import Path


class WorkspaceBusyError(RuntimeError):
    """Another ZX Code process currently owns this workspace lock."""


class WorkspaceLock:
    """Non-blocking flock held for the full chat task lifetime.

    This coordinates ZX Code processes that use the same lock path. It cannot
    prevent edits from IDEs, shells, build daemons, or other non-cooperating tools.
    """
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()
        self.lock_path = self.workspace / ".zx_checkpoints" / "workspace.lock"
        self._fd = None

    def acquire(self) -> "WorkspaceLock":
        if os.name != "posix":
            raise RuntimeError("workspace locking currently requires POSIX flock")
        root = self.workspace / ".zx_checkpoints"
        if root.is_symlink():
            raise RuntimeError("checkpoint directory cannot be a symlink")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        self._fd = os.open(self.lock_path, flags, 0o600)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self._fd)
            self._fd = None
            raise WorkspaceBusyError(
                "workspace is already being used by another ZX Code task") from exc
        metadata = json.dumps({"pid": os.getpid(), "host": socket.gethostname(),
                               "started_at": time.time()}).encode("utf-8")
        os.ftruncate(self._fd, 0)
        os.write(self._fd, metadata)
        os.fsync(self._fd)
        return self

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "WorkspaceLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
