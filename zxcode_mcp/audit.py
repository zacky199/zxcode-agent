"""Privacy-preserving, hash-chained local MCP audit log."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ZERO_HASH = "0" * 64


class MCPAuditLog:
    """Append-only JSONL audit events; never records tool arguments or results."""
    def __init__(self, path: str | Path, *, max_bytes: int = 8_000_000):
        self.path = Path(path).expanduser()
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._secure_parent()

    def _secure_parent(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        if self.path.is_symlink():
            raise OSError("MCP audit log must not be a symlink")
        if self.path.exists():
            info = self.path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("MCP audit log must be a regular file with no hard links")
            if info.st_size > self.max_bytes:
                raise OSError("MCP audit log exceeds configured size limit")
            os.chmod(self.path, 0o600)

    @staticmethod
    def _digest(entry: dict[str, Any]) -> str:
        canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def append(self, *, event: str, server: str = "", tool: str = "", risk: str = "unknown",
               policy: str = "approval", decision: str = "pending", status: str = "pending",
               error_type: str = "") -> dict:
        # Fixed allowlisted fields; secrets, arguments, descriptions and outputs are excluded.
        with self._lock:
            self._secure_parent()
            previous = ZERO_HASH
            if self.path.exists() and self.path.stat().st_size:
                chain = self.verify_unlocked()
                if not chain["valid"]:
                    raise OSError(f"MCP audit integrity failure at line {chain.get('failed_line')}; refusing append")
                previous = chain["last_hash"]
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "event": str(event)[:64], "server": str(server)[:64], "tool": str(tool)[:96],
                "risk": str(risk)[:32], "policy": str(policy)[:32],
                "decision": str(decision)[:32], "status": str(status)[:32],
                "error_type": str(error_type)[:64], "previous_hash": previous,
            }
            record["hash"] = self._digest(record)
            encoded = (json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n").encode()
            current = self.path.stat().st_size if self.path.exists() else 0
            if current + len(encoded) > self.max_bytes:
                raise OSError("MCP audit log size limit reached; execution blocked")
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
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as stream:
                for number, line in enumerate(stream, 1):
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                        claimed = record.pop("hash")
                        linked = record.get("previous_hash")
                        if linked != previous or claimed != self._digest(record):
                            return {"valid": False, "entries": count, "failed_line": number}
                    except Exception:
                        return {"valid": False, "entries": count, "failed_line": number}
                    previous = claimed
                    count += 1
        return {"valid": True, "entries": count, "last_hash": previous}


    def verify(self) -> dict:
        with self._lock:
            self._secure_parent()
            return self.verify_unlocked()
