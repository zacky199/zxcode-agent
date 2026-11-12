import logging
from typing import Dict, List

from .sdk import BasePlugin, PluginContext

log = logging.getLogger("zx.plugins")


class PluginRegistry:
    """Registry for active plugins with transactional lifecycle management."""

    def __init__(self):
        self.plugins: Dict[str, BasePlugin] = {}
        self._plugin_tool_names: Dict[str, List[str]] = {}
        self._plugin_contexts: Dict[str, PluginContext] = {}

    def register(self, plugin: BasePlugin, ctx: PluginContext = None) -> None:
        name = plugin.display_name
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Plugin must have a non-empty name")
        if name in self.plugins:
            raise ValueError(f"Duplicate plugin name: {name}")

        base_context = ctx or PluginContext()
        plugin_context = base_context.for_registration()
        try:
            plugin.initialize(plugin_context)
            tool_names = plugin_context.commit_tool_handlers()
        except Exception:
            plugin_context.rollback_tool_handlers()
            try:
                plugin.shutdown()
            except Exception:
                log.warning("plugin cleanup failed after initialization error: %s", name)
            raise

        self.plugins[name] = plugin
        self._plugin_tool_names[name] = tool_names
        self._plugin_contexts[name] = plugin_context

    def unregister(self, name: str) -> None:
        plugin = self.plugins.pop(name, None)
        context = self._plugin_contexts.pop(name, None)
        self._plugin_tool_names.pop(name, None)
        if context is not None:
            context.rollback_tool_handlers()
        if plugin is not None:
            try:
                plugin.shutdown()
            except Exception:
                log.warning("plugin shutdown failed: %s", name)

    def rollback_to(self, existing_names) -> None:
        keep = set(existing_names)
        for name in list(self.plugins):
            if name not in keep:
                self.unregister(name)

    def all(self) -> List[BasePlugin]:
        return list(self.plugins.values())

    def get(self, name: str) -> BasePlugin:
        return self.plugins.get(name)

    def shutdown_all(self) -> None:
        for name in list(self.plugins):
            self.unregister(name)

    def collect_tool_schemas(self) -> List[Dict]:
        schemas = []
        names = set()
        for plugin in self.plugins.values():
            plugin_schemas = plugin.get_tools()
            if plugin_schemas is None:
                continue
            if not isinstance(plugin_schemas, list):
                raise TypeError(f"Plugin {plugin.display_name} returned invalid tool schemas")
            for schema in plugin_schemas:
                if not isinstance(schema, dict):
                    raise TypeError(f"Plugin {plugin.display_name} returned an invalid tool schema")
                function = schema.get("function")
                name = function.get("name") if isinstance(function, dict) else None
                if schema.get("type") != "function" or not isinstance(name, str) or not name.strip():
                    raise ValueError(f"Plugin {plugin.display_name} returned an invalid function schema")
                if name in names:
                    raise ValueError(f"Duplicate plugin tool name: {name}")
                names.add(name)
                schemas.append(schema)
        return schemas

    def dispatch_event(self, event: Dict) -> None:
        for name, plugin in list(self.plugins.items()):
            try:
                plugin.on_event(event)
            except Exception as exc:
                log.error("plugin disabled after event failure (%s): %s", name, exc)
                self.unregister(name)
