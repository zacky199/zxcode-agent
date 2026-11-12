import os
import secrets
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Optional, Tuple


class DirectoryIdentityError(RuntimeError):
    """A parent directory chain cannot be proven to be the recorded one."""


@dataclass(frozen=True)
class DirectoryComponent:
    name: str
    device: Optional[int]
    inode: Optional[int]

    def manifest(self):
        return {"name": self.name, "device": self.device, "inode": self.inode}


@dataclass(frozen=True)
class ParentDirectoryState:
    relative_parent: str
    workspace_device: int
    workspace_inode: int
    components: Tuple[DirectoryComponent, ...]

    def manifest(self):
        return {
            "relative_parent": self.relative_parent,
            "workspace_device": self.workspace_device,
            "workspace_inode": self.workspace_inode,
            "components": [component.manifest() for component in self.components],
        }


class ParentDirectoryAnchor:
    """Pinned directory descriptors for one workspace-relative target parent."""

    def __init__(self, workspace: Path, workspace_fd: int,
                 state: ParentDirectoryState, target_relative: str):
        FileSystemUtils.require_dirfd_mutations()
        target = PurePosixPath(target_relative)
        if (target.is_absolute() or not target.parts
                or any(part in ("", ".", "..") for part in target.parts)):
            raise DirectoryIdentityError("unsafe target path for parent directory anchor")
        expected_parent = target.parent.as_posix()
        if expected_parent == ".":
            expected_parent = "."
        if state.relative_parent != expected_parent:
            raise DirectoryIdentityError("parent directory anchor does not match target path")

        self.workspace = Path(workspace)
        self.target_relative = target.as_posix()
        self.target_name = target.name
        self._fds = []
        self._closed = False
        self._root_fd = os.dup(workspace_fd)
        self._fds.append(self._root_fd)
        self._root_identity = (state.workspace_device, state.workspace_inode)
        self._components = []
        try:
            self._verify_root()
            parent_fd = self._root_fd
            for component in state.components:
                child_fd = None
                if component.device is None or component.inode is None:
                    raise DirectoryIdentityError(
                        f"parent directory identity is missing at {component.name}; "
                        "automatic parent creation is disabled fail-closed")
                else:
                    try:
                        info = os.stat(component.name, dir_fd=parent_fd,
                                       follow_symlinks=False)
                    except OSError as exc:
                        raise DirectoryIdentityError(
                            f"parent directory identity changed at {component.name}: {exc}") from exc
                    if (info.st_dev, info.st_ino) != (component.device, component.inode):
                        raise DirectoryIdentityError(
                            f"parent directory identity changed at {component.name}")
                if not stat.S_ISDIR(info.st_mode):
                    raise DirectoryIdentityError(
                        f"parent directory identity is not a directory at {component.name}")
                if child_fd is None:
                    try:
                        child_fd = os.open(component.name,
                                           FileSystemUtils._directory_open_flags(),
                                           dir_fd=parent_fd)
                    except OSError as exc:
                        raise DirectoryIdentityError(
                            f"parent directory identity could not be pinned at {component.name}: {exc}") from exc
                    opened = os.fstat(child_fd)
                    if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                            or not stat.S_ISDIR(opened.st_mode)):
                        os.close(child_fd)
                        raise DirectoryIdentityError(
                            f"parent directory identity changed while opening {component.name}")
                self._fds.append(child_fd)
                self._components.append(
                    DirectoryComponent(component.name, opened.st_dev, opened.st_ino))
                parent_fd = child_fd
            self.parent_fd = parent_fd
            self.state = ParentDirectoryState(
                state.relative_parent, self._root_identity[0],
                self._root_identity[1], tuple(self._components))
            self.verify_live()
        except Exception:
            self.close()
            raise

    @property
    def fd(self) -> int:
        if self._closed:
            raise DirectoryIdentityError("parent directory anchor is closed")
        return self.parent_fd

    def _verify_root(self):
        root_info = os.fstat(self._root_fd)
        if (not stat.S_ISDIR(root_info.st_mode)
                or (root_info.st_dev, root_info.st_ino) != self._root_identity):
            raise DirectoryIdentityError("workspace directory identity changed")
        try:
            path_info = os.stat(self.workspace, follow_symlinks=False)
        except OSError as exc:
            raise DirectoryIdentityError(
                f"workspace directory identity is uncertain: {exc}") from exc
        if (not stat.S_ISDIR(path_info.st_mode)
                or (path_info.st_dev, path_info.st_ino) != self._root_identity):
            raise DirectoryIdentityError("workspace directory identity changed")

    def verify_live(self):
        """Re-walk from the pinned workspace fd and compare each dev+ino pair."""
        if self._closed:
            raise DirectoryIdentityError("parent directory anchor is closed")
        self._verify_root()
        current_fd = os.dup(self._root_fd)
        try:
            for component in self._components:
                try:
                    info = os.stat(component.name, dir_fd=current_fd,
                                   follow_symlinks=False)
                except OSError as exc:
                    raise DirectoryIdentityError(
                        f"parent directory identity changed at {component.name}: {exc}") from exc
                if (not stat.S_ISDIR(info.st_mode)
                        or (info.st_dev, info.st_ino) != (component.device, component.inode)):
                    raise DirectoryIdentityError(
                        f"parent directory identity changed at {component.name}")
                try:
                    next_fd = os.open(component.name,
                                      FileSystemUtils._directory_open_flags(),
                                      dir_fd=current_fd)
                except OSError as exc:
                    raise DirectoryIdentityError(
                        f"parent directory identity changed at {component.name}: {exc}") from exc
                opened = os.fstat(next_fd)
                if ((opened.st_dev, opened.st_ino) != (component.device, component.inode)
                        or not stat.S_ISDIR(opened.st_mode)):
                    os.close(next_fd)
                    raise DirectoryIdentityError(
                        f"parent directory identity changed while re-opening {component.name}")
                os.close(current_fd)
                current_fd = next_fd
            pinned = os.fstat(self.parent_fd)
            current = os.fstat(current_fd)
            if (pinned.st_dev, pinned.st_ino) != (current.st_dev, current.st_ino):
                raise DirectoryIdentityError("parent directory identity changed at target boundary")
        finally:
            try:
                os.close(current_fd)
            except OSError:
                pass

    def assert_target(self, file_path: Path):
        if Path(file_path).name != self.target_name:
            raise DirectoryIdentityError("target name does not match pinned parent directory")

    def stat_entry(self, name: str):
        try:
            return os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        except FileNotFoundError:
            return None

    def open_entry(self, name: str, flags: int, mode: int = 0o600) -> int:
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        return os.open(name, flags | nofollow, mode, dir_fd=self.fd)

    def create_temp_file(self, prefix: str, mode: int = 0o600):
        flags = (os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | getattr(os, "O_NOFOLLOW", 0))
        for _ in range(128):
            name = prefix + secrets.token_hex(12)
            try:
                fd = os.open(name, flags, mode, dir_fd=self.fd)
                return fd, name
            except FileExistsError:
                continue
        raise OSError("could not allocate unique dirfd-relative temporary file")

    def rename(self, source: str, destination: str):
        os.rename(source, destination, src_dir_fd=self.fd, dst_dir_fd=self.fd)

    def link(self, source: str, destination: str):
        os.link(source, destination, src_dir_fd=self.fd, dst_dir_fd=self.fd,
                follow_symlinks=False)

    def unlink(self, name: str):
        os.unlink(name, dir_fd=self.fd)

    def mkdir(self, name: str, mode: int = 0o700):
        os.mkdir(name, mode=mode, dir_fd=self.fd)

    def rmdir(self, name: str):
        os.rmdir(name, dir_fd=self.fd)

    def read_regular_file(self, name: str):
        self.verify_live()
        entry_info = self.stat_entry(name)
        if entry_info is None or not stat.S_ISREG(entry_info.st_mode):
            raise DirectoryIdentityError("pinned target is not a regular file")
        fd = self.open_entry(name, os.O_RDONLY)
        try:
            before = os.fstat(fd)
            signature = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink > 1
                    or signature(before) != signature(entry_info)):
                raise DirectoryIdentityError("target changed while opening through parent dirfd")
            chunks = []
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            after = os.fstat(fd)
            data = b"".join(chunks)
            if signature(before) != signature(after) or len(data) != before.st_size:
                raise DirectoryIdentityError("target changed while reading through parent dirfd")
            self.verify_live()
            return data, before
        finally:
            os.close(fd)

    def close(self):
        if self._closed:
            return
        self._closed = True
        for fd in reversed(self._fds):
            try:
                os.close(fd)
            except OSError:
                pass
        self._fds.clear()


class FileSystemUtils:
    @staticmethod
    def is_hard_linked_regular_file(file_path: Path) -> bool:
        """Fail closed for regular files with multiple hard links or stat errors."""
        try:
            info = Path(file_path).stat()
            return stat.S_ISREG(info.st_mode) and getattr(info, "st_nlink", 1) > 1
        except (OSError, RuntimeError, ValueError):
            return True

    @staticmethod
    def require_dirfd_mutations():
        required = (os.open, os.stat, os.mkdir, os.rename, os.unlink, os.link, os.rmdir)
        if (not all(operation in os.supports_dir_fd for operation in required)
                or not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW")):
            raise DirectoryIdentityError(
                "native mutations require POSIX dirfd/O_DIRECTORY/O_NOFOLLOW support; "
                "operation blocked fail-closed")

    @staticmethod
    def _directory_open_flags():
        return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    @staticmethod
    def _verify_workspace_path(workspace: Path, expected) -> None:
        try:
            info = os.stat(workspace, follow_symlinks=False)
        except OSError as exc:
            raise DirectoryIdentityError(
                f"workspace directory identity is uncertain: {exc}") from exc
        if (not stat.S_ISDIR(info.st_mode)
                or (info.st_dev, info.st_ino) != (expected.st_dev, expected.st_ino)):
            raise DirectoryIdentityError("workspace directory identity changed")

    @staticmethod
    def open_workspace_directory(workspace: Path) -> int:
        FileSystemUtils.require_dirfd_mutations()
        fd = os.open(os.fspath(workspace), FileSystemUtils._directory_open_flags())
        try:
            info = os.fstat(fd)
            if not stat.S_ISDIR(info.st_mode):
                raise DirectoryIdentityError("workspace is not a directory")
            FileSystemUtils._verify_workspace_path(Path(workspace), info)
            return fd
        except Exception:
            os.close(fd)
            raise

    @staticmethod
    def capture_parent_directory_state(workspace_fd: int, workspace: Path,
                                       relative_parent: str) -> ParentDirectoryState:
        FileSystemUtils.require_dirfd_mutations()
        parent = PurePosixPath(str(relative_parent))
        if parent.is_absolute() or any(part in ("", "..") for part in parent.parts):
            raise DirectoryIdentityError("unsafe parent directory path")
        parts = () if str(parent) in ("", ".") else parent.parts
        root_info = os.fstat(workspace_fd)
        if not stat.S_ISDIR(root_info.st_mode):
            raise DirectoryIdentityError("workspace directory handle is invalid")
        FileSystemUtils._verify_workspace_path(Path(workspace), root_info)
        fd = os.dup(workspace_fd)
        components = []
        missing_seen = False
        try:
            for name in parts:
                if missing_seen:
                    components.append(DirectoryComponent(name, None, None))
                    continue
                try:
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    components.append(DirectoryComponent(name, None, None))
                    missing_seen = True
                    continue
                if not stat.S_ISDIR(info.st_mode):
                    raise DirectoryIdentityError(
                        f"parent component is not a real directory: {name}")
                child_fd = os.open(name, FileSystemUtils._directory_open_flags(), dir_fd=fd)
                child_info = os.fstat(child_fd)
                if ((child_info.st_dev, child_info.st_ino) != (info.st_dev, info.st_ino)
                        or not stat.S_ISDIR(child_info.st_mode)):
                    os.close(child_fd)
                    raise DirectoryIdentityError(
                        f"parent directory identity changed while capturing {name}")
                os.close(fd)
                fd = child_fd
                components.append(
                    DirectoryComponent(name, child_info.st_dev, child_info.st_ino))
            state = ParentDirectoryState(
                str(parent) if str(parent) not in ("", ".") else ".",
                root_info.st_dev, root_info.st_ino, tuple(components))
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
        FileSystemUtils.verify_parent_directory_state(workspace_fd, workspace, state)
        return state

    @staticmethod
    def verify_parent_directory_state(workspace_fd: int, workspace: Path,
                                      state: ParentDirectoryState) -> None:
        FileSystemUtils.require_dirfd_mutations()
        root_info = os.fstat(workspace_fd)
        if ((root_info.st_dev, root_info.st_ino)
                != (state.workspace_device, state.workspace_inode)):
            raise DirectoryIdentityError("workspace directory identity changed")
        FileSystemUtils._verify_workspace_path(Path(workspace), root_info)
        fd = os.dup(workspace_fd)
        missing_seen = False
        try:
            for component in state.components:
                if missing_seen:
                    if component.device is not None or component.inode is not None:
                        raise DirectoryIdentityError("invalid parent identity after missing component")
                    continue
                try:
                    info = os.stat(component.name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    if component.device is not None or component.inode is not None:
                        raise DirectoryIdentityError(
                            f"parent directory identity changed at {component.name}")
                    missing_seen = True
                    continue
                if component.device is None or component.inode is None:
                    raise DirectoryIdentityError(
                        f"parent directory identity changed: missing {component.name} appeared")
                if (not stat.S_ISDIR(info.st_mode)
                        or (info.st_dev, info.st_ino) != (component.device, component.inode)):
                    raise DirectoryIdentityError(
                        f"parent directory identity changed at {component.name}")
                child_fd = os.open(component.name, FileSystemUtils._directory_open_flags(),
                                   dir_fd=fd)
                child_info = os.fstat(child_fd)
                if ((child_info.st_dev, child_info.st_ino) != (info.st_dev, info.st_ino)
                        or not stat.S_ISDIR(child_info.st_mode)):
                    os.close(child_fd)
                    raise DirectoryIdentityError(
                        f"parent directory identity changed while verifying {component.name}")
                os.close(fd)
                fd = child_fd
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    @staticmethod
    def open_parent_anchor(workspace_fd: int, workspace: Path,
                           state: ParentDirectoryState,
                           target_relative: str) -> ParentDirectoryAnchor:
        return ParentDirectoryAnchor(workspace, workspace_fd, state,
                                     target_relative)

    @staticmethod
    def read_text_at(anchor: ParentDirectoryAnchor, encoding: str = "utf-8"):
        data, info = anchor.read_regular_file(anchor.target_name)
        return data.decode(encoding), stat.S_IMODE(info.st_mode) & 0o777

    @staticmethod
    def atomic_copy(source, file_path: Path, mode: int = None) -> None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(dir=file_path.parent)
        try:
            with os.fdopen(fd, "wb") as target:
                shutil.copyfileobj(source, target)
            if mode is not None:
                os.chmod(temp_path, mode)
            os.replace(temp_path, file_path)
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    @staticmethod
    def _atomic_write_path(file_path: Path, content: str, encoding: str,
                           mode: int = None) -> None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(dir=file_path.parent, text=True)
        try:
            with os.fdopen(fd, "w", encoding=encoding, newline="") as tmp:
                tmp.write(content)
                tmp.flush()
                os.fsync(tmp.fileno())
            if mode is not None:
                os.chmod(temp_path, mode)
            os.replace(temp_path, file_path)
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    @staticmethod
    def atomic_write(file_path: Path, content: str, encoding: str = "utf-8",
                     mode: int = None,
                     before_replace: Callable[[str, ParentDirectoryAnchor], None] = None,
                     retain_displaced: Callable[[str, ParentDirectoryAnchor], None] = None,
                     parent_anchor: ParentDirectoryAnchor = None) -> None:
        if before_replace is None:
            if parent_anchor is not None or retain_displaced is not None:
                raise RuntimeError("dirfd parent anchor requires the native journal guard")
            FileSystemUtils._atomic_write_path(file_path, content, encoding, mode)
            return
        if not callable(retain_displaced) or parent_anchor is None:
            raise RuntimeError("guarded atomic writes require a pinned parent dirfd and preimage retention")
        parent_anchor.assert_target(file_path)
        parent_anchor.verify_live()
        parent_fd = parent_anchor.fd
        target_name = parent_anchor.target_name
        fd = temp_name = None
        quarantine_name = None
        try:
            fd, temp_name = parent_anchor.create_temp_file(".zx-native-stage-")
            with os.fdopen(fd, "w", encoding=encoding, newline="") as tmp:
                fd = None
                tmp.write(content)
                tmp.flush()
                os.fsync(tmp.fileno())
                if mode is not None:
                    os.fchmod(tmp.fileno(), mode)
            parent_anchor.verify_live()
            current = parent_anchor.stat_entry(target_name)
            if current is not None:
                before_replace(target_name, parent_anchor)
                qfd, quarantine_name = parent_anchor.create_temp_file(".zx-native-current-")
                os.close(qfd)
                parent_anchor.rename(target_name, quarantine_name)
                try:
                    before_replace(quarantine_name, parent_anchor)
                    try:
                        parent_anchor.link(temp_name, target_name)
                    except OSError:
                        if parent_anchor.stat_entry(target_name) is not None:
                            before_replace(target_name, parent_anchor)
                        raise
                    before_replace(quarantine_name, parent_anchor)
                    retain_displaced(quarantine_name, parent_anchor)
                    quarantine_name = None
                    parent_anchor.verify_live()
                except Exception as exc:
                    restored = False
                    if parent_anchor.stat_entry(target_name) is None:
                        try:
                            parent_anchor.link(quarantine_name, target_name)
                            parent_anchor.unlink(quarantine_name)
                            quarantine_name = None
                            restored = True
                        except OSError:
                            pass
                    if not restored and quarantine_name is not None:
                        raise RuntimeError(
                            f"concurrent file state preserved in pinned parent: {exc}") from exc
                    raise
            else:
                before_replace(target_name, parent_anchor)
                try:
                    parent_anchor.link(temp_name, target_name)
                except OSError:
                    if parent_anchor.stat_entry(target_name) is not None:
                        before_replace(target_name, parent_anchor)
                    raise
                parent_anchor.verify_live()
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            for temp in (temp_name,):
                if temp is not None:
                    try:
                        parent_anchor.unlink(temp)
                    except OSError:
                        pass
            # A quarantine name is deliberately retained when ownership or parent
            # identity became uncertain; the journal reports the conflict.

    @staticmethod
    def safe_relative_path(target: Path, base: Path) -> Path:
        """Return a target relative to base, raising ValueError if it escapes."""
        return Path(target).resolve().relative_to(Path(base).resolve())

    @staticmethod
    def is_within_workspace(target_path: Path, workspace: Path) -> bool:
        try:
            resolved_target = Path(target_path).resolve()
            resolved_workspace = Path(workspace).resolve()
            return (resolved_target == resolved_workspace
                    or resolved_workspace in resolved_target.parents)
        except (OSError, RuntimeError, ValueError):
            return False
