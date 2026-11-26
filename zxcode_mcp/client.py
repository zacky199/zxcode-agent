"""MCP SDK transport adapter. The official SDK is an optional dependency."""
from __future__ import annotations

import asyncio
import os
import shutil
import stat
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any, Dict, Optional
from urllib.parse import urlsplit
import re

import httpx

from .security import MCPServerConfig, validate_endpoint_resolution


def build_stdio_environment(config: MCPServerConfig) -> Dict[str, str]:
    """Return only explicitly configured child-process variables.

    The official SDK merges this mapping over its own small documented
    baseline allowlist (HOME, LOGNAME, PATH, SHELL, TERM, USER). It does not
    inherit the full parent environment. Explicit values override that base.
    """
    return dict(config.env)


class _PinnedNetworkBackend:
    """Resolve TCP connections only to addresses approved by DNS preflight.

    TLS remains owned by httpcore, which receives the original URL hostname
    and therefore keeps certificate verification and SNI bound to that name.
    """

    def __init__(self, backend, hostname: str, addresses):
        self._backend = backend
        self._hostname = hostname.rstrip(".").lower()
        self._addresses = tuple(addresses)
        if not self._addresses:
            raise ValueError("Pinned MCP endpoint has no approved addresses")

    async def connect_tcp(self, host, port, timeout=None, local_address=None,
                          socket_options=None):
        requested_host = host.decode("ascii") if isinstance(host, bytes) else str(host)
        if requested_host.rstrip(".").lower() != self._hostname:
            raise OSError("MCP transport attempted an unpinned hostname connection")
        last_error = None
        for address in self._addresses:
            try:
                return await self._backend.connect_tcp(
                    address, port, timeout=timeout, local_address=local_address,
                    socket_options=socket_options,
                )
            except OSError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise OSError("No approved MCP endpoint address could be connected")

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        return await self._backend.connect_unix_socket(
            path, timeout=timeout, socket_options=socket_options
        )

    async def sleep(self, seconds):
        return await self._backend.sleep(seconds)


class _PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """httpx transport with connection-time DNS pinning (fail closed)."""

    def __init__(self, hostname: str, addresses, *, limits: httpx.Limits):
        super().__init__(trust_env=False, limits=limits, retries=0, http2=False)
        # httpx currently does not expose httpcore's network_backend through
        # its public transport constructor. Fail closed if this internal seam
        # changes; never silently fall back to ordinary DNS resolution.
        pool = getattr(self, "_pool", None)
        backend = getattr(pool, "_network_backend", None)
        if pool is None or backend is None:
            raise RuntimeError(
                "Unsupported httpx/httpcore transport internals; refusing unpinned MCP connection"
            )
        pool._network_backend = _PinnedNetworkBackend(backend, hostname, addresses)


def build_pinned_httpx_client_factory(url: str, addresses):
    """Create the official MCP SDK's HTTPX factory with DNS-pinned TCP."""
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").rstrip(".").lower()
    pinned_addresses = tuple(addresses)

    def factory(headers=None, timeout=None, auth=None):
        # Bound outbound concurrency and disable redirect following. Redirects could
        # otherwise attempt a second origin; the pinned backend also fails closed
        # if the SDK/httpcore asks to connect to a hostname outside this endpoint.
        limits = httpx.Limits(max_connections=4, max_keepalive_connections=2,
                             keepalive_expiry=15.0)
        transport = _PinnedAsyncHTTPTransport(hostname, pinned_addresses, limits=limits)
        return httpx.AsyncClient(
            transport=transport, headers=headers, timeout=timeout, auth=auth,
            trust_env=False, follow_redirects=False,
        )

    return factory


def build_docker_stdio_command(config: MCPServerConfig):
    """Build a restrictive Docker command for a digest-pinned stdio server.

    The host working directory is mounted read-only at /workspace. No host
    home, Docker socket, network, capabilities, or writable root filesystem
    are exposed to the container. The image must already exist locally.
    """
    if config.transport != "stdio" or not config.sandbox_image or not config.cwd:
        raise ValueError("Docker stdio isolation requires stdio transport, image digest, and cwd")
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("Docker CLI is unavailable; refusing unsandboxed fallback")
    docker_path = os.path.realpath(docker)
    info = os.stat(docker_path)
    if not stat.S_ISREG(info.st_mode) or not os.access(docker_path, os.X_OK):
        raise RuntimeError("Docker CLI path is not a regular executable")
    host_cwd = os.path.realpath(config.cwd)
    if not os.path.isdir(host_cwd):
        raise RuntimeError("Docker sandbox working directory is not a directory")
    if "," in host_cwd:
        raise RuntimeError("Docker sandbox working directory cannot contain commas")
    args = [
        "run", "--rm", "--interactive", "--pull=never",
        "--network=none", "--read-only", "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true", "--pids-limit=64",
        "--memory=256m", "--memory-swap=256m", "--cpus=1.0",
        "--ulimit=nofile=1024:1024", "--ulimit=core=0:0", "--shm-size=16m",
        f"--user={os.getuid()}:{os.getgid()}",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=64m",
        "--entrypoint", config.command,
        "--mount", f"type=bind,src={host_cwd},dst=/workspace,readonly",
        "--workdir=/workspace",
    ]
    for key in sorted(config.env):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", key):
            raise ValueError("Invalid environment variable name for Docker sandbox")
        args.extend(("--env", key))
    args.append(config.sandbox_image)
    args.extend(config.args)
    return docker_path, args


class MCPProtocolError(RuntimeError):
    """Connection, protocol, or remote-tool failure."""


class MCPClient:
    def __init__(self, config: MCPServerConfig):
        self.config = config

    @asynccontextmanager
    async def session(self):
        try:
            from mcp import ClientSession
            if self.config.transport == "streamable-http":
                from mcp.client.streamable_http import streamablehttp_client
                # Resolve once, then pin every TCP connection to those exact
                # addresses. The custom backend preserves the original host
                # for HTTP Host, TLS SNI, and certificate verification.
                pinned_addresses = await asyncio.wait_for(
                    asyncio.to_thread(validate_endpoint_resolution, self.config.url),
                    timeout=self.config.timeout_seconds,
                )
                httpx_factory = build_pinned_httpx_client_factory(
                    self.config.url, pinned_addresses
                )
                async with streamablehttp_client(
                    self.config.url,
                    headers=dict(self.config.headers),
                    timeout=timedelta(seconds=self.config.timeout_seconds),
                    sse_read_timeout=timedelta(seconds=self.config.timeout_seconds),
                    httpx_client_factory=httpx_factory,
                ) as (read_stream, write_stream, _get_session_id):
                    async with ClientSession(
                        read_stream, write_stream,
                        read_timeout_seconds=timedelta(seconds=self.config.timeout_seconds),
                    ) as session:
                        await asyncio.wait_for(session.initialize(), timeout=self.config.timeout_seconds)
                        yield session
            else:
                from mcp.client.stdio import stdio_client, StdioServerParameters
                if self.config.sandbox_image:
                    command, args = build_docker_stdio_command(self.config)
                    params = StdioServerParameters(
                        command=command, args=args,
                        env=build_stdio_environment(self.config), cwd=None,
                    )
                else:
                    params = StdioServerParameters(
                        command=self.config.command,
                        args=list(self.config.args),
                        env=build_stdio_environment(self.config),
                        cwd=self.config.cwd,
                    )
                async with stdio_client(params) as (read_stream, write_stream):
                    async with ClientSession(
                        read_stream, write_stream,
                        read_timeout_seconds=timedelta(seconds=self.config.timeout_seconds),
                    ) as session:
                        await asyncio.wait_for(session.initialize(), timeout=self.config.timeout_seconds)
                        yield session
        except ImportError as exc:
            raise MCPProtocolError(
                "MCP SDK is not installed. Install ZX Code with the [mcp] extra."
            ) from exc
        except MCPProtocolError:
            raise
        except Exception as exc:
            # Remote exception text may contain echoed credentials, request
            # headers, filesystem paths, or attacker-controlled content.
            raise MCPProtocolError(
                f"MCP {self.config.name} transport/protocol failure ({type(exc).__name__})"
            ) from None

    async def list_tools(self):
        async with self.session() as session:
            result = await asyncio.wait_for(
                session.list_tools(), timeout=self.config.timeout_seconds
            )
            return list(result.tools)

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]):
        async with self.session() as session:
            result = await asyncio.wait_for(
                session.call_tool(tool_name, arguments=arguments),
                timeout=self.config.timeout_seconds,
            )
            if getattr(result, "isError", False):
                raise MCPProtocolError(f"Remote MCP tool '{tool_name}' returned an error")
            return result
