"""Fail-closed command execution using Docker/Podman OS isolation.

Host execution is retained only as an explicit, visibly unsafe opt-in for
compatibility and constrained development environments.
"""
import os
import shutil
import signal
import subprocess
import threading
import tempfile
import warnings
from pathlib import Path
from typing import Callable, Optional, Tuple


class SandboxUnavailable(RuntimeError):
    """Raised when an OS sandbox is required but no runtime is available."""


class SandboxExecutor:
    def __init__(self, workspace: Path, default_timeout: float = 60.0,
                 command_authorizer: Callable[[str], bool] = None, *,
                 mode: str = "container", runtime: Optional[str] = None,
                 image: str = "zxcode-sandbox:latest", network: str = "none",
                 memory_limit: str = "2g", cpu_limit: str = "2",
                 pids_limit: int = 256, allow_host_execution: bool = False):
        self.workspace = Path(workspace).resolve()
        self.default_timeout = float(default_timeout)
        self.command_authorizer = command_authorizer
        if mode not in {"container", "host"}:
            raise ValueError("sandbox mode must be 'container' or 'host'")
        if network not in {"none", "bridge"}:
            raise ValueError("network must be 'none' or 'bridge'")
        if mode == "host" and not allow_host_execution:
            raise ValueError("host execution requires allow_host_execution=True")
        self.mode = mode
        # An explicitly configured runtime must be honored exactly; silently
        # falling back to another installed runtime breaks fail-closed checks.
        if runtime is not None:
            self.runtime = shutil.which(runtime) or runtime
        else:
            self.runtime = shutil.which("podman") or shutil.which("docker")
        self.image = image
        self.network = network
        self.memory_limit = memory_limit
        self.cpu_limit = str(cpu_limit)
        self.pids_limit = int(pids_limit)
        self._approval_execution_lock = threading.Lock()

    def _terminate_process_tree(self, process: subprocess.Popen) -> None:
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except (OSError, ProcessLookupError):
            try:
                process.kill()
            except OSError:
                pass

    def _container_argv(self, command: str, cidfile: Optional[str] = None,
                        read_only_workspace: bool = False):
        runtime_path = Path(self.runtime) if self.runtime else None
        if (runtime_path is None or not runtime_path.is_file()
                or not os.access(runtime_path, os.X_OK)):
            raise SandboxUnavailable(
                f"Docker/Podman runtime unavailable or not executable: {self.runtime!r}; "
                "host fallback is disabled"
            )
        args = [self.runtime, "run", "--rm", "--init", "--pull=never", "--read-only",
                "--network", self.network, "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit", str(self.pids_limit),
                "--memory", self.memory_limit, "--cpus", self.cpu_limit,
                "--tmpfs", "/tmp:rw,nosuid,size=512m",
                "--env", "HOME=/tmp", "--env", "TMPDIR=/tmp",
                "--env", "PIP_CACHE_DIR=/tmp/pip-cache",
                "--env", "npm_config_cache=/tmp/npm-cache",
                "--env", "GOCACHE=/tmp/go-cache", "--env", "GOMODCACHE=/tmp/go-mod"]
        if cidfile:
            args += ["--cidfile", cidfile]
        if os.name == "posix" and hasattr(os, "getuid"):
            args += ["--user", f"{os.getuid()}:{os.getgid()}"]
        # Only the selected workspace is writable inside the container.
        mount_mode = "ro" if read_only_workspace else "rw"
        mount = f"{self.workspace}:/workspace:{mount_mode}"
        args += ["--volume", mount, "--workdir", "/workspace", self.image,
                 "sh", "-c", command]
        return args

    def run_verification(self, cmd: str, timeout: float = None) -> Tuple[int, str, str]:
        """Run verification with a read-only workspace mount; no host fallback."""
        if self.mode != "container":
            return 125, "", "VERIFICATION BLOCKED: container sandbox is required for read-only verification"
        return self.run_command(cmd, timeout=timeout, read_only_workspace=True)

    def run_command(self, cmd: str, timeout: float = None, *,
                    read_only_workspace: bool = False) -> Tuple[int, str, str]:
        if not isinstance(cmd, str) or not cmd.strip():
            return 126, "", "ERROR: command must be a non-empty string"
        exec_timeout = self.default_timeout if timeout is None else float(timeout)
        if exec_timeout <= 0:
            return 126, "", "ERROR: timeout must be positive"
        if not self.workspace.is_dir() or self.workspace.is_symlink():
            return 126, "", "ERROR: workspace must be an existing non-symlink directory"
        if not callable(self.command_authorizer):
            return 126, "", "ERROR: explicit command authorization/approval is required"
        with self._approval_execution_lock:
            try:
                authorized = self.command_authorizer(cmd)
            except Exception:
                authorized = None
            if authorized is not True:
                return 126, "", "ERROR: explicit command authorization/approval was not granted"
            try:
                if self.mode == "host":
                    if read_only_workspace:
                        return 125, "", "VERIFICATION BLOCKED: read-only workspace verification requires container sandbox mode"
                    warnings.warn("UNSAFE HOST EXECUTION: command inherits ZX Code process privileges; no OS sandbox is active", RuntimeWarning)
                    argv = ["/bin/sh", "-lc", cmd] if os.name == "posix" else ["cmd", "/c", cmd]
                    cwd = self.workspace
                else:
                    cid_handle = tempfile.NamedTemporaryFile(prefix="zx-cid-", delete=False)
                    cid_path = cid_handle.name
                    cid_handle.close()
                    os.unlink(cid_path)
                    argv = self._container_argv(cmd, cidfile=cid_path,
                                                read_only_workspace=read_only_workspace)
                    cwd = self.workspace
                process = subprocess.Popen(argv, shell=False, cwd=cwd,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    start_new_session=(os.name == "posix"))
                try:
                    stdout, stderr = process.communicate(timeout=exec_timeout)
                    if self.mode == "container":
                        Path(cid_path).unlink(missing_ok=True)
                    return process.returncode, stdout.strip(), stderr.strip()
                except subprocess.TimeoutExpired:
                    self._terminate_process_tree(process)
                    try:
                        process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.communicate()
                    if self.mode == "container":
                        try:
                            cid = Path(cid_path).read_text(encoding="utf-8").strip()
                            if cid:
                                subprocess.run([self.runtime, "kill", cid], capture_output=True, timeout=10, check=False)
                                subprocess.run([self.runtime, "rm", "-f", cid], capture_output=True, timeout=10, check=False)
                        except (OSError, subprocess.TimeoutExpired):
                            pass
                    if self.mode == "container":
                        Path(cid_path).unlink(missing_ok=True)
                    return -1, "", f"TIMEOUT: command exceeded {exec_timeout} seconds; process tree terminated; container cleanup requested"
            except SandboxUnavailable as exc:
                return 125, "", f"SANDBOX UNAVAILABLE: {exc}"
            except Exception as exc:
                return -1, "", f"ERROR: {type(exc).__name__}: {exc}"
