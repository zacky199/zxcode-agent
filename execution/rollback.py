import hashlib
import os
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Dict, Set

from config.constants import CHECKPOINT_DIR_NAME
from utils.filesystem import FileSystemUtils
from utils.sensitive_files import is_sensitive_path


class RollbackManager:
    """Restore snapshots only from validated paths inside the workspace."""

    PROTECTED_DIRS = {
        ".git", "node_modules", "__pycache__", ".zx_checkpoints", ".zxagent",
        "venv", ".next", ".nuxt", ".cache", "coverage", "dist", "build",
    }

    def __init__(self, workspace: Path, trusted_snapshots: Dict[Path, str] = None):
        self.workspace = Path(workspace).resolve()
        self.checkpoint_dir = self.workspace / CHECKPOINT_DIR_NAME
        self.trusted_snapshots = trusted_snapshots if trusted_snapshots is not None else {}

    def _validate_snapshot_source(self, zip_path: Path):
        original = Path(zip_path)
        if original.is_symlink():
            raise ValueError("Snapshot source may not be a symlink")
        source = Path(os.path.abspath(original))
        if not source.exists() or not source.is_file() or source.suffix.lower() != ".zip":
            raise FileNotFoundError(f"Snapshot tidak valid: {source}")
        if self.checkpoint_dir.is_symlink():
            raise ValueError("Checkpoint directory may not be a symlink")
        checkpoint_root = Path(os.path.abspath(self.checkpoint_dir))
        if source.parent != checkpoint_root:
            raise ValueError("Snapshot source must be inside the active checkpoint directory")
        resolved = source.resolve(strict=True)
        try:
            resolved.relative_to(checkpoint_root.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise ValueError("Snapshot source escapes checkpoint directory") from exc
        expected_digest = self.trusted_snapshots.get(resolved)
        if expected_digest is None:
            raise ValueError("Snapshot was not created by the active checkpoint manager")
        return source, expected_digest

    @staticmethod
    def _member_path(info: zipfile.ZipInfo) -> Path | None:
        """Return a normalized relative path, rejecting traversal and special files."""
        name = info.filename
        if not name or "\\" in name:
            raise ValueError(f"Invalid snapshot path: {name!r}")
        windows_path = PureWindowsPath(name)
        if name.startswith("/") or windows_path.is_absolute() or windows_path.drive:
            raise ValueError(f"Absolute snapshot path is not allowed: {name!r}")

        raw_parts = name[:-1].split("/") if info.is_dir() and name.endswith("/") else name.split("/")
        if (not raw_parts or any(part in ("", ".", "..") or ":" in part
                                 for part in raw_parts)):
            raise ValueError(f"Unsafe snapshot path: {name!r}")
        if any(part.startswith(".") for part in raw_parts):
            raise ValueError(f"Snapshots may not write hidden paths: {name!r}")
        if any(part in RollbackManager.PROTECTED_DIRS for part in raw_parts):
            raise ValueError(f"Snapshot may not write to protected paths: {name!r}")
        if is_sensitive_path(Path(*raw_parts)):
            raise ValueError(f"Snapshot may not write credential-like paths: {name!r}")

        mode = info.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if kind and kind not in (stat.S_IFREG, stat.S_IFDIR):
            raise ValueError(f"Special files are not allowed in snapshots: {name!r}")
        if info.is_dir():
            return None
        return Path(*raw_parts)

    def _validate_target(self, relative_path: Path) -> Path:
        target = self.workspace.joinpath(relative_path)
        current = self.workspace
        for part in relative_path.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(f"Snapshot target traverses a symlink: {relative_path}")
            try:
                current.resolve(strict=False).relative_to(self.workspace)
            except ValueError as exc:
                raise ValueError(f"Snapshot target escapes workspace: {relative_path}") from exc
        return target

    def _validated_entries(self, archive: zipfile.ZipFile) -> Dict[Path, zipfile.ZipInfo]:
        entries: Dict[Path, zipfile.ZipInfo] = {}
        for info in archive.infolist():
            relative_path = self._member_path(info)
            if relative_path is None:
                continue
            self._validate_target(relative_path)
            if relative_path in entries:
                raise ValueError(f"Duplicate snapshot path: {relative_path}")
            entries[relative_path] = info

        paths = set(entries)
        for relative_path in paths:
            for parent in relative_path.parents:
                if parent != Path(".") and parent in paths:
                    raise ValueError(f"Conflicting snapshot paths: {parent} and {relative_path}")
        return entries

    def _snapshot_entries(self, zip_path: Path) -> Set[Path]:
        with zipfile.ZipFile(zip_path) as archive:
            return set(self._validated_entries(archive))

    def workspace_matches_snapshot(self, zip_path: Path) -> bool:
        """Return whether tracked workspace files still equal this trusted snapshot."""
        try:
            source_path, expected_digest = self._validate_snapshot_source(Path(zip_path))
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(source_path, flags)
            with os.fdopen(fd, "rb") as source:
                digest = hashlib.sha256()
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
                if digest.hexdigest() != expected_digest:
                    return False
                source.seek(0)
                with zipfile.ZipFile(source) as archive:
                    entries = self._validated_entries(archive)
                    current_files: Dict[Path, Path] = {}
                    for root, dirs, files in os.walk(self.workspace, topdown=True, followlinks=False):
                        root_path = Path(root)
                        dirs[:] = [
                            dirname for dirname in dirs
                            if dirname not in self.PROTECTED_DIRS and not dirname.startswith(".")
                            and not (root_path / dirname).is_symlink()
                            and not is_sensitive_path(root_path / dirname)
                        ]
                        for filename in files:
                            path = root_path / filename
                            if (filename.startswith(".") or path.is_symlink()
                                    or is_sensitive_path(path) or not path.is_file()
                                    or FileSystemUtils.is_hard_linked_regular_file(path)):
                                continue
                            try:
                                relative_path = path.relative_to(self.workspace)
                            except ValueError:
                                continue
                            current_files[relative_path] = path

                    if set(current_files) != set(entries):
                        return False
                    for relative_path, info in entries.items():
                        with archive.open(info) as archived, current_files[relative_path].open("rb") as current:
                            while True:
                                archived_chunk = archived.read(1024 * 1024)
                                current_chunk = current.read(1024 * 1024)
                                if archived_chunk != current_chunk:
                                    return False
                                if not archived_chunk:
                                    break
                    return True
        except Exception:
            # An inconclusive comparison must not suppress rollback of a task-local snapshot.
            return False

    def restore_snapshot(self, zip_path: Path) -> bool:
        zip_path, expected_digest = self._validate_snapshot_source(Path(zip_path))

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(zip_path, flags)
        with os.fdopen(fd, "rb") as source:
            digest = hashlib.sha256()
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
            if digest.hexdigest() != expected_digest:
                raise ValueError("Trusted snapshot content has changed")
            source.seek(0)
            with zipfile.ZipFile(source) as archive:
                # Validate every path before making any filesystem change.
                entries = self._validated_entries(archive)
                try:
                    for relative_path, info in entries.items():
                        target = self._validate_target(relative_path)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if info.create_system == 3:
                            mode = stat.S_IMODE(info.external_attr >> 16) & 0o777
                        else:
                            try:
                                mode = stat.S_IMODE(target.stat().st_mode) & 0o777
                            except OSError:
                                mode = None
                        with archive.open(info) as src:
                            FileSystemUtils.atomic_copy(src, target, mode=mode)

                    # Remove workspace files created after the snapshot, without following symlinks.
                    for root, dirs, files in os.walk(self.workspace, topdown=True, followlinks=False):
                        root_path = Path(root)
                        kept_dirs = []
                        for dirname in dirs:
                            child = root_path / dirname
                            if (dirname in self.PROTECTED_DIRS or dirname.startswith(".")
                                    or is_sensitive_path(child)):
                                continue
                            if child.is_symlink():
                                try:
                                    child.unlink()
                                except OSError:
                                    pass
                                continue
                            kept_dirs.append(dirname)
                        dirs[:] = kept_dirs

                        for filename in files:
                            path = root_path / filename
                            if (filename.startswith(".") or is_sensitive_path(path)
                                    or FileSystemUtils.is_hard_linked_regular_file(path)):
                                continue
                            relative_path = path.relative_to(self.workspace)
                            if relative_path not in entries:
                                try:
                                    path.unlink()
                                except OSError:
                                    pass
                    return True
                except Exception as exc:
                    raise RuntimeError(f"Gagal melakukan rollback: {exc}") from exc
