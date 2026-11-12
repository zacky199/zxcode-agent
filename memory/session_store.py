"""Persist the conversation per workspace so a session can be resumed later."""
import hashlib
import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.constants import DEFAULT_CONFIG_DIR

SESSION_VERSION = 1
_ALLOWED_ROLES = {"user", "assistant", "system"}


class SessionStore:
    """Atomic, private (0700 dir / 0600 file) JSON store keyed by workspace path.

    Sessions can contain whatever the user typed, so they are kept in the
    per-user config directory, never inside the workspace. Every method fails
    soft: persistence problems must never break the agent loop.
    """

    MAX_MESSAGES = 60
    MAX_CONTENT_CHARS = 20_000
    MAX_FILE_BYTES = 5_000_000

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = Path(base_dir) if base_dir else DEFAULT_CONFIG_DIR / "sessions"

    # ------------------------------------------------------------------
    def _key(self, workspace) -> str:
        resolved = str(Path(workspace).resolve())
        return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:16]

    def path_for(self, workspace) -> Path:
        return self.base_dir / f"{self._key(workspace)}.json"

    @classmethod
    def _clean(cls, history: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        cleaned: List[Dict[str, str]] = []
        for message in history or []:
            if not isinstance(message, dict):
                continue
            role, content = message.get("role"), message.get("content")
            if role not in _ALLOWED_ROLES or not isinstance(content, str) or not content:
                continue
            cleaned.append({"role": role, "content": content[:cls.MAX_CONTENT_CHARS]})
        return cleaned[-cls.MAX_MESSAGES:]

    # ------------------------------------------------------------------
    def save(self, workspace, history: List[Dict[str, Any]]) -> bool:
        try:
            if self.base_dir.is_symlink():
                return False
            self.base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.base_dir, stat.S_IRWXU)
            target = self.path_for(workspace)
            if target.is_symlink():
                return False
            payload = {
                "version": SESSION_VERSION,
                "workspace": str(Path(workspace).resolve()),
                "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "messages": self._clean(history),
            }
            fd, tmp = tempfile.mkstemp(prefix=".session-", dir=self.base_dir)
            try:
                os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, target)
            except Exception:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            return True
        except (OSError, ValueError, TypeError):
            return False

    def load(self, workspace) -> List[Dict[str, str]]:
        try:
            path = self.path_for(workspace)
            if path.is_symlink() or not path.is_file():
                return []
            if path.stat().st_size > self.MAX_FILE_BYTES:
                return []
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, dict) or data.get("version") != SESSION_VERSION:
            return []
        if data.get("workspace") != str(Path(workspace).resolve()):
            return []
        messages = data.get("messages")
        return self._clean(messages) if isinstance(messages, list) else []

    def clear(self, workspace) -> None:
        try:
            path = self.path_for(workspace)
            if path.is_file() and not path.is_symlink():
                path.unlink()
        except OSError:
            pass
