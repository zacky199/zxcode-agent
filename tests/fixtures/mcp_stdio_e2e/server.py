"""Minimal MCP stdio server used only by Docker end-to-end tests."""
import json
import os
import resource
import socket
from pathlib import Path

from mcp.server.fastmcp import FastMCP

server = FastMCP("zxcode-mcp-e2e-fixture")


@server.tool()
def echo(message: str) -> str:
    """Return the supplied message with a deterministic fixture prefix."""
    return f"fixture-echo:{message}"


@server.tool()
def probe_isolation() -> str:
    """Report real container restrictions observed by the server process."""
    def can_write(path: str) -> bool:
        try:
            probe = Path(path) / ".zxcode-mcp-isolation-probe"
            probe.write_text("probe", encoding="utf-8")
            probe.unlink()
            return True
        except OSError:
            return False

    network_blocked = False
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        sock.connect(("1.1.1.1", 443))
    except OSError:
        network_blocked = True
    finally:
        sock.close()

    status = Path("/proc/self/status").read_text(encoding="utf-8")
    status_fields = dict(
        line.split(":", 1) for line in status.splitlines() if ":" in line
    )

    def cgroup_value(name: str) -> str:
        try:
            return Path("/sys/fs/cgroup", name).read_text(encoding="utf-8").strip()
        except OSError:
            return "unavailable"

    def mount_options(target: str) -> set[str]:
        for line in Path("/proc/mounts").read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if len(fields) >= 4 and fields[1] == target:
                return set(fields[3].split(","))
        return set()

    nofile_soft, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    core_soft, _ = resource.getrlimit(resource.RLIMIT_CORE)
    tmp_bytes = os.statvfs("/tmp").f_frsize * os.statvfs("/tmp").f_blocks
    shm_bytes = os.statvfs("/dev/shm").f_frsize * os.statvfs("/dev/shm").f_blocks
    tmp_options = mount_options("/tmp")

    return json.dumps({
        "root_writable": can_write("/etc"),
        "workspace_writable": can_write("/workspace"),
        "tmp_writable": can_write("/tmp"),
        "tmp_noexec": "noexec" in tmp_options,
        "tmp_nosuid": "nosuid" in tmp_options,
        "tmp_nodev": "nodev" in tmp_options,
        "tmp_max_bytes": tmp_bytes,
        "shm_max_bytes": shm_bytes,
        "external_network_blocked": network_blocked,
        "docker_socket_visible": Path("/var/run/docker.sock").exists(),
        "effective_capabilities": status_fields.get("CapEff", "").strip(),
        "no_new_privileges": status_fields.get("NoNewPrivs", "").strip(),
        "nofile_soft_limit": nofile_soft,
        "core_soft_limit": core_soft,
        "pids_limit": cgroup_value("pids.max"),
        "memory_limit": cgroup_value("memory.max"),
        "cpu_limit": cgroup_value("cpu.max"),
    }, sort_keys=True)


if __name__ == "__main__":
    server.run(transport="stdio")
