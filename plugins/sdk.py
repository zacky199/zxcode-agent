"""ZX Agent Plugin SDK for trusted, in-process extensions.

Plugins are Python code executed with the same OS permissions as the agent.
They are not isolated or sandboxed; only load code you trust.
"""

import logging
from abc import ABC
from typing import Any, Dict, List, Optional


class PluginContext:
    """Runtime context passed to a plugin during initialization."""

    def __init__(self, workspace=None, tool_engine=None, memory=None,
                 logger: Optional[logging.Logger] = None, *, _staging: bool = False):
        self.workspace = workspace
        self.tool_engine = tool_engine
        self.memory = memory
        self.log = logger or logging.getLogger("zx.plugin")
        self._staging = _staging
        self._pending_tool_handlers: Dict[str, Any] = {}
        self._committed_tool_names: List[str] = []

    def for_registration(self) -> "PluginContext":
        """Create a per-plugin context that stages registrations until init succeeds."""
        return PluginContext(
            workspace=self.workspace,
            tool_engine=self.tool_engine,
            memory=self.memory,
            logger=self.log,
            _staging=True,
        )

    def register_tool_handler(self, name: str, handler) -> None:
        """Register a custom tool handler, staging it during initialization."""
        if not isinstance(name, str) or not name or not callable(handler):
            raise ValueError("Tool name and handler must be valid")
        if self._staging:
            if name in self._pending_tool_handlers:
                raise ValueError(f"Duplicate plugin tool handler: {name}")
            self._pending_tool_handlers[name] = handler
            return
        if self.tool_engine is not None and hasattr(self.tool_engine, "register_plugin_tool"):
            self.tool_engine.register_plugin_tool(name, handler)
        else:
            self.log.warning("tool engine does not support plugin tool registration")

    def commit_tool_handlers(self) -> List[str]:
        """Commit staged handlers only after the plugin initialized successfully."""
        registered: List[str] = []
        try:
            if self.tool_engine is not None:
                for name, handler in self._pending_tool_handlers.items():
                    self.tool_engine.register_plugin_tool(name, handler)
                    registered.append(name)
        except Exception:
            for name in reversed(registered):
                self.tool_engine.unregister_plugin_tool(name)
            raise
        self._committed_tool_names = registered
        self._pending_tool_handlers.clear()
        self._staging = False
        return list(registered)

    def rollback_tool_handlers(self) -> None:
        if self.tool_engine is not None:
            for name in reversed(self._committed_tool_names):
                self.tool_engine.unregister_plugin_tool(name)
        self._committed_tool_names.clear()
        self._pending_tool_handlers.clear()


class BasePlugin(ABC):
    """Base class every trusted external plugin must subclass."""

    name: str = ""
    description: str = ""
    version: str = "0.1.0"
    api_version: int = 1

    @property
    def display_name(self) -> str:
        return self.name or type(self).__name__

    def initialize(self, ctx: PluginContext) -> None:
        self.ctx = ctx

    def shutdown(self) -> None:
        """Release plugin-owned resources."""

    def get_tools(self) -> List[Dict[str, Any]]:
        return []

    def execute_tool(self, name: str, args: Dict[str, Any]) -> Any:
        return f"Tool '{name}' is not handled by this plugin."

    def on_event(self, event: Dict[str, Any]) -> None:
        pass

    def transform_user_prompt(self, prompt: str) -> str:
        return prompt

    __abstract_methods__ = None
