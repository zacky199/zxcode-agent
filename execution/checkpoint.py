import hashlib
import os
import re
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from config.constants import CHECKPOINT_DIR_NAME
from utils.filesystem import FileSystemUtils
from utils.sensitive_files import is_sensitive_path


class CheckpointManager:
    """Create workspace snapshots while keeping output paths inside the workspace."""

    _SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()
        self.checkpoint_dir = self.workspace / CHECKPOINT_DIR_NAME
        if self.checkpoint_dir.is_symlink():
            raise ValueError("Checkpoint directory must not be a symlink")
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.checkpoint_dir.resolve().relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError("Checkpoint directory escapes workspace") from exc
        os.chmod(self.checkpoint_dir, 0o700)
        self.exclude = {
            ".git", CHECKPOINT_DIR_NAME, "node_modules", "__pycache__", "venv",
            ".next", ".nuxt", ".cache", "coverage", "dist", "build",
        }
        # Only snapshots created by this manager during this process/session are trusted.
        self.trusted_snapshots: Dict[Path, str] = {}

    def create_snapshot(self, name: str = "snapshot") -> Path:
        if not isinstance(name, str) or not self._SAFE_NAME.fullmatch(name):
            raise ValueError(f"Invalid checkpoint name: {name!r}")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        out_path = self.checkpoint_dir / f"{name}_{timestamp}.zip"
        if self.checkpoint_dir.is_symlink() or out_path.is_symlink():
            raise ValueError("Checkpoint output path must not traverse a symlink")
        try:
            out_path.resolve(strict=False).relative_to(self.checkpoint_dir.resolve())
        except ValueError as exc:
            raise ValueError("Checkpoint output escapes checkpoint directory") from exc

        # Build beside the destination, then publish atomically. A killed process
        # may leave a private temp file, but never a partially named checkpoint.
        fd, temp_name = tempfile.mkstemp(prefix=".checkpoint-", suffix=".tmp",
                                         dir=self.checkpoint_dir)
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for root, dirs, files in os.walk(self.workspace, topdown=True, followlinks=False):
                    dirs[:] = [
                        dirname for dirname in dirs
                        if dirname not in self.exclude and not dirname.startswith(".")
                        and not (Path(root) / dirname).is_symlink()
                        and not is_sensitive_path(Path(root) / dirname)
                    ]
                    for filename in files:
                        if filename.startswith("."):
                            continue
                        full_path = Path(root) / filename
                        # Never follow file symlinks: they may expose data outside the workspace.
                        if (full_path.is_symlink() or is_sensitive_path(full_path)
                                or not full_path.is_file()
                                or FileSystemUtils.is_hard_linked_regular_file(full_path)):
                            continue
                        try:
                            archive_name = str(full_path.relative_to(self.workspace))
                        except ValueError:
                            continue
                        # Do not silently publish a partial snapshot when a file
                        # cannot be read; fail closed and discard the temporary ZIP.
                        archive.write(full_path, arcname=archive_name)
            os.chmod(temp_path, 0o600)
            with temp_path.open("rb") as stream:
                os.fsync(stream.fileno())
            os.replace(temp_path, out_path)
            if hasattr(os, "O_DIRECTORY"):
                dir_fd = os.open(self.checkpoint_dir, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        self.trusted_snapshots[out_path.resolve()] = self._sha256_file(out_path)
        return out_path

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def latest_snapshot(self, prefix: str = "pre_mutation") -> Optional[Path]:
        """Return the newest extant snapshot registered by this active manager."""
        for path in reversed(tuple(self.trusted_snapshots)):
            if prefix and not path.name.startswith(f"{prefix}_"):
                continue
            if path.exists() and path.is_file() and not path.is_symlink():
                return path
        return None
