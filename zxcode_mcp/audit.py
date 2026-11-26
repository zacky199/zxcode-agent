"""Privacy-preserving, hash-chained local MCP audit log with bounded rotation."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
import fcntl
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ZERO_HASH = "0" * 64


class MCPAuditLog:
    """Append-only JSONL audit segments; archives are retained, never auto-deleted."""
    def __init__(self, path: str | Path, *, max_bytes: int = 8_000_000, max_archives: int = 5):
        self.path = Path(path).expanduser()
        self.max_bytes = max_bytes
        self.max_archives = max_archives
        if max_bytes < 256 or max_archives < 0:
            raise ValueError("max_bytes must be >=256 and max_archives must be >=0")
        self._lock = threading.Lock()
        self._lock_path = self.path.with_name(self.path.name + ".lock")
        self._secure_parent()

    def _secure_parent(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        self._secure_file(self.path, "MCP audit log")
        self._secure_file(self._lock_path, "MCP audit lock")
        for archive in self.archive_paths():
            self._secure_file(archive, "MCP audit archive")

    @staticmethod
    def _secure_file(path: Path, label: str):
        if path.is_symlink():
            raise OSError(f"{label} must not be a symlink")
        if path.exists():
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError(f"{label} must be a regular file with no hard links")
            os.chmod(path, 0o600)

    @contextmanager
    def _process_lock(self):
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self._lock_path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("MCP audit lock must be a regular file with no hard links")
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def archive_paths(self):
        # Sequential segment numbering is checked by verify(); gaps are integrity errors.
        return sorted(self.path.parent.glob(self.path.stem + ".[0-9][0-9][0-9][0-9][0-9]" + self.path.suffix))

    @staticmethod
    def _digest(entry: dict[str, Any]) -> str:
        canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def append(self, *, event: str, server: str = "", tool: str = "", risk: str = "unknown",
               policy: str = "approval", decision: str = "pending", status: str = "pending",
               error_type: str = "") -> dict:
        with self._lock, self._process_lock():
            self._secure_parent()
            chain = self.verify_unlocked()
            if not chain["valid"]:
                raise OSError(f"MCP audit integrity failure at line {chain.get('failed_line')}; refusing append")
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "event": str(event)[:64], "server": str(server)[:64], "tool": str(tool)[:96],
                "risk": str(risk)[:32], "policy": str(policy)[:32],
                "decision": str(decision)[:32], "status": str(status)[:32],
                "error_type": str(error_type)[:64], "previous_hash": chain["last_hash"],
            }
            record["hash"] = self._digest(record)
            encoded = (json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n").encode()
            current_size = self.path.stat().st_size if self.path.exists() else 0
            if len(encoded) > self.max_bytes:
                raise OSError("MCP audit event exceeds segment size limit; execution blocked")
            if current_size + len(encoded) > self.max_bytes:
                archives = self.archive_paths()
                if len(archives) >= self.max_archives:
                    raise OSError("MCP audit size limit reached (archive limit); execution blocked without deleting evidence")
                archive = self.path.with_name(f"{self.path.stem}.{len(archives)+1:05d}{self.path.suffix}")
                if archive.exists() or archive.is_symlink():
                    raise OSError("MCP audit archive destination already exists; refusing overwrite")
                os.replace(self.path, archive)
                os.chmod(archive, 0o600)
                dirfd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(dirfd)
                finally:
                    os.close(dirfd)
                current_size = 0
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(self.path, flags, 0o600)
            try:
                os.write(fd, encoded)
                os.fsync(fd)
            finally:
                os.close(fd)
            return record

    def verify_unlocked(self) -> dict:
        previous = ZERO_HASH
        count = 0
        files = self.archive_paths() + ([self.path] if self.path.exists() else [])
        archives = files[:-1] if self.path.exists() else files
        expected_archives = [self.path.with_name(f"{self.path.stem}.{i:05d}{self.path.suffix}") for i in range(1, len(archives) + 1)]
        if archives != expected_archives:
            return {"valid": False, "entries": 0, "failed_line": 0, "reason": "archive sequence gap"}
        for file in files:
            try:
                self._secure_file(file, "MCP audit segment")
                with file.open("r", encoding="utf-8") as stream:
                    for number, line in enumerate(stream, 1):
                        if not line.strip():
                            continue
                        try:
                            record = json.loads(line)
                            claimed = record.pop("hash")
                            if record.get("previous_hash") != previous or claimed != self._digest(record):
                                return {"valid": False, "entries": count, "failed_line": number, "file": file.name}
                        except Exception:
                            return {"valid": False, "entries": count, "failed_line": number, "file": file.name}
                        previous = claimed
                        count += 1
            except OSError as exc:
                return {"valid": False, "entries": count, "failed_line": 0, "file": file.name, "reason": type(exc).__name__}
        return {"valid": True, "entries": count, "last_hash": previous, "segments": len(files)}

    def verify(self) -> dict:
        with self._lock, self._process_lock():
            self._secure_parent()
            return self.verify_unlocked()
