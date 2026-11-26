"""Private, atomic persistence for non-secret MCP server configuration."""
from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Dict

from .security import MCPSecurityError, MCPServerConfig


class MCPConfigStore:
    VERSION = 1

    def __init__(self, path: Path):
        self.path = Path(path).expanduser()

    def _secure_parent(self) -> None:
        parent = self.path.parent
        if parent.is_symlink():
            raise OSError("MCP configuration directory must not be a symlink")
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(parent, stat.S_IRWXU)
        if self.path.is_symlink():
            raise OSError("MCP configuration file must not be a symlink")

    def load(self) -> Dict[str, Dict[str, Any]]:
        self._secure_parent()
        if not self.path.exists():
            return {}
        info = self.path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError("MCP configuration must be a regular file without hard links")
        if info.st_size > 256_000:
            raise MCPSecurityError("MCP configuration exceeds 256 KB")
        os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("version") != self.VERSION:
            raise MCPSecurityError("Unsupported or malformed MCP configuration file")
        servers = raw.get("servers")
        if not isinstance(servers, dict) or len(servers) > 64:
            raise MCPSecurityError("MCP server configuration must be an object with at most 64 entries")
        validated = {}
        for name, item in servers.items():
            if not isinstance(item, dict):
                raise MCPSecurityError(f"Invalid MCP configuration for server {name!r}")
            allowed = {"name", "transport", "url", "command", "args", "cwd", "sandbox_image",
                       "header_env", "header_vault", "allowed_tools", "timeout_seconds", "max_response_bytes",
                       "policy_mode", "denied_tools"}
            if set(item) - allowed or item.get("name") != name:
                raise MCPSecurityError(f"Unexpected or inconsistent MCP configuration for {name!r}")
            args = item.get("args", [])
            allowed_tools = item.get("allowed_tools", [])
            if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
                raise MCPSecurityError(f"Invalid stdio arguments for server {name!r}")
            if not isinstance(allowed_tools, list) or any(not isinstance(tool, str) for tool in allowed_tools):
                raise MCPSecurityError(f"Invalid tool allowlist for server {name!r}")
            policy_mode = item.get("policy_mode", "approval")
            denied_tools = item.get("denied_tools", [])
            if not isinstance(policy_mode, str) or policy_mode not in {"approval", "read_only", "deny"}:
                raise MCPSecurityError(f"Invalid MCP policy mode for {name!r}")
            if not isinstance(denied_tools, list) or len(denied_tools) > 128 or any(
                not isinstance(tool, str) or not tool or len(tool) > 128 for tool in denied_tools
            ):
                raise MCPSecurityError(f"Invalid MCP denied-tools list for {name!r}")
            header_env = item.get("header_env", {})
            if not isinstance(header_env, dict) or any(
                not isinstance(k, str) or not isinstance(v, str) for k, v in header_env.items()
            ):
                raise MCPSecurityError(f"Invalid HTTP header environment references for {name!r}")
            header_vault = item.get("header_vault", {})
            if not isinstance(header_vault, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in header_vault.items()):
                raise MCPSecurityError(f"Invalid HTTP header vault references for {name!r}")
            config = MCPServerConfig(
                name=name, transport=item.get("transport", "streamable-http"),
                url=item.get("url"), command=item.get("command"),
                args=tuple(args), cwd=item.get("cwd"), sandbox_image=item.get("sandbox_image"),
                header_env=header_env, header_vault=header_vault,
                allowed_tools=frozenset(allowed_tools),
                timeout_seconds=item.get("timeout_seconds", 20.0),
                max_response_bytes=item.get("max_response_bytes", 1_000_000),
            )
            validated[name] = self.serialize(config)
            validated[name]["policy_mode"] = policy_mode
            validated[name]["denied_tools"] = sorted(set(denied_tools))
        return validated

    @staticmethod
    def serialize(config: MCPServerConfig) -> Dict[str, Any]:
        return {
            "name": config.name,
            "transport": config.transport,
            "url": config.url,
            "command": config.command,
            "args": list(config.args),
            "cwd": config.cwd,
            "sandbox_image": config.sandbox_image,
            "header_env": dict(config.header_env),
            "header_vault": dict(config.header_vault),
            "allowed_tools": sorted(config.allowed_tools),
            "timeout_seconds": config.timeout_seconds,
            "max_response_bytes": config.max_response_bytes,
        }

    def save(self, servers: Dict[str, MCPServerConfig], policies=None) -> None:
        policies = policies or {}
        self._secure_parent()
        payload = {
            "version": self.VERSION,
            "servers": {name: {**self.serialize(config),
                            "policy_mode": getattr(policies.get(name), "mode", "approval"),
                            "denied_tools": sorted(getattr(policies.get(name), "denied_tools", ())) }
                       for name, config in sorted(servers.items())},
        }
        encoded = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        fd, temp_name = tempfile.mkstemp(prefix=".mcp-config-", dir=str(self.path.parent))
        try:
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
            try:
                dir_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
